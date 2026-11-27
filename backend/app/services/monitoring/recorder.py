"""Metrics recorder: every LLM call, Graph request, background job and API request, shared by
all processes (API, worker, worker-extract) through Redis. Modelled on Alpha-CV's monitor.

Recording is fire-and-forget. Call sites append to an in-memory deque (microseconds, never
raises, never blocks); one background thread per process drains it into Redis about once a
second, so recording adds no latency, and an unreachable Redis only loses buffered metrics -
never a request or an extraction.

Redis layout - every key expires, so monitoring can never fill Redis:
  mon:m:<minute>:<kind>|<role>|<model>   hash  calls, errors, total_ms, tokens_in, tokens_out, cost, b0..bN
  mon:idx:<minute>                       set   the hashes that exist for that minute
  mon:events                             list  recent notable events (LLM calls, jobs, failures), newest first
  mon:proc:<process id>                  hash  heartbeat of each live process (expires after 30 s)

Kinds: "llm" (role = extraction step: pass1, pass2, alternates, roster, chat...), "graph"
(role = list / thread / mime / attachment), "job" (role = Celery task name), "api"
(role = route). Counters are HINCRBY, so any number of processes write at once safely.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("monitoring")

LATENCY_BUCKETS_MS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 120000, 300000)
EVENTS_KEY = "mon:events"
METRIC_TTL_S = 8 * 24 * 3600
EVENT_CAP = 3000
LISTED_KINDS = ("llm", "job")      # every call of these is kept as an event; others only on failure
HEARTBEAT_TTL_S = 30

_client = None
_client_failed_at = 0.0


def redis_client():
    """A sync Redis client, or None when Redis is unreachable (the monitor then shows nothing)."""
    global _client, _client_failed_at
    if _client is not None:
        return _client
    if time.time() - _client_failed_at < 30:
        return None
    try:
        import redis

        from app.core.config import settings
        c = redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
        c.ping()
        _client = c
        return c
    except Exception:
        _client_failed_at = time.time()
        return None


def bucket_index(ms: float) -> int:
    for i, edge in enumerate(LATENCY_BUCKETS_MS):
        if ms <= edge:
            return i
    return len(LATENCY_BUCKETS_MS)


def percentile_from_buckets(buckets: list[int], q: float) -> float | None:
    total = sum(buckets)
    if total == 0:
        return None
    target, running = q * total, 0
    for i, count in enumerate(buckets):
        running += count
        if running >= target:
            return float(LATENCY_BUCKETS_MS[i]) if i < len(LATENCY_BUCKETS_MS) else float(LATENCY_BUCKETS_MS[-1] * 2)
    return float(LATENCY_BUCKETS_MS[-1] * 2)


_ROLE_RE = re.compile(r"^([a-z]+(?:-[a-z]{2,}(?=-|$))?)")


def role_from_label(label: str) -> str:
    """'pass1-batch3' -> 'pass1', 'pass2-recover-A4' -> 'pass2-recover', 'roster-census-p2' -> 'roster-census'."""
    label = (label or "").strip().lower()
    if not label:
        return "other"
    m = re.match(r"^(pass[12](?:-(?:rescue|recover))?)", label)
    if m:
        return m.group(1)
    m = _ROLE_RE.match(label)
    return (m.group(1) if m else label)[:40].lower()


@dataclass
class _Record:
    ts: float
    kind: str
    role: str
    model: str
    duration_ms: float
    status: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    notable: bool = False


def _default_kind() -> str:
    argv = " ".join(sys.argv)
    if "celery" in argv:
        return "worker-extract" if "-Q extract" in argv or "extract@" in argv else "worker"
    return "api"


class Recorder:
    def __init__(self, max_buffer: int = 20_000) -> None:
        self._buf: deque[_Record] = deque(maxlen=max_buffer)
        self.dropped = 0
        self.kind = _default_kind()
        self.started_at = time.time()
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pid = os.getpid()

    @property
    def process_id(self) -> str:
        return f"{socket.gethostname()}:{os.getpid()}"

    # ---- call-site API (never raises) -------------------------------------------------
    def record(self, kind: str, role: str, model: str, duration_ms: float, *, status: str = "ok",
               tokens_in: int = 0, tokens_out: int = 0, cost: float = 0.0,
               detail: dict[str, Any] | None = None, notable: bool = False) -> None:
        try:
            self._ensure_started()
            if len(self._buf) == self._buf.maxlen:
                self.dropped += 1
            self._buf.append(_Record(time.time(), kind, role or "other", model or "-", float(duration_ms),
                                     status, int(tokens_in or 0), int(tokens_out or 0), float(cost or 0.0),
                                     detail or {}, notable or status != "ok" or kind in LISTED_KINDS))
            with self._lock:
                key = f"{kind}:{role}"
                self._counters[key] = self._counters.get(key, 0) + 1
        except Exception:  # monitoring must never break the work it watches
            pass

    def start(self) -> None:
        """Start the heartbeat now, so the process shows as online before it records anything."""
        try:
            self._ensure_started()
        except Exception:
            pass

    # ---- background flush -------------------------------------------------------------
    def _ensure_started(self) -> None:
        if os.getpid() != self._pid:            # forked (Celery prefork child): fresh state
            self._pid = os.getpid()
            self._thread = None
            self._lock = threading.Lock()
            self.started_at = time.time()
            self._counters = {}
        if self._thread is None or not self._thread.is_alive():
            with self._lock:
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._loop, name="metrics-flush", daemon=True)
                    self._thread.start()

    def _loop(self) -> None:
        last_beat = 0.0
        while True:
            time.sleep(1.0)
            self.flush()
            if time.time() - last_beat >= 5.0:
                self._heartbeat()
                last_beat = time.time()

    def flush(self) -> None:
        if not self._buf:
            return
        records: list[_Record] = []
        while self._buf and len(records) < 5000:
            try:
                records.append(self._buf.popleft())
            except IndexError:
                break
        r = redis_client()
        if r is None:
            self.dropped += len(records)
            return
        try:
            pipe = r.pipeline(transaction=False)
            events = []
            for rec in records:
                minute = int(rec.ts // 60)
                member = f"{rec.kind}|{rec.role}|{rec.model}"
                key = f"mon:m:{minute}:{member}"
                pipe.hincrby(key, "calls", 1)
                if rec.status != "ok":
                    pipe.hincrby(key, "errors", 1)
                pipe.hincrbyfloat(key, "total_ms", rec.duration_ms)
                if rec.tokens_in:
                    pipe.hincrby(key, "tokens_in", rec.tokens_in)
                if rec.tokens_out:
                    pipe.hincrby(key, "tokens_out", rec.tokens_out)
                if rec.cost:
                    pipe.hincrbyfloat(key, "cost", rec.cost)
                pipe.hincrby(key, f"b{bucket_index(rec.duration_ms)}", 1)
                pipe.expire(key, METRIC_TTL_S)
                idx = f"mon:idx:{minute}"
                pipe.sadd(idx, member)
                pipe.expire(idx, METRIC_TTL_S)
                if rec.notable:
                    events.append(json.dumps({
                        "ts": round(rec.ts, 3), "kind": rec.kind, "role": rec.role, "model": rec.model,
                        "ms": round(rec.duration_ms, 1), "status": rec.status, "tokens_in": rec.tokens_in,
                        "tokens_out": rec.tokens_out, "cost": round(rec.cost, 6), "detail": rec.detail,
                        "proc": self.kind}))
            if events:
                pipe.lpush(EVENTS_KEY, *events)
                pipe.ltrim(EVENTS_KEY, 0, EVENT_CAP - 1)
                pipe.expire(EVENTS_KEY, METRIC_TTL_S)
            pipe.execute()
        except Exception:
            self.dropped += len(records)
            log.debug("metrics flush failed", exc_info=True)

    def _heartbeat(self) -> None:
        r = redis_client()
        if r is None:
            return
        try:
            with self._lock:
                counters = dict(self._counters)
            key = f"mon:proc:{self.process_id}"
            r.hset(key, mapping={
                "kind": self.kind, "host": socket.gethostname(), "pid": os.getpid(),
                "started_at": self.started_at, "beat": time.time(),
                "stats": json.dumps({"counters": counters, "dropped": self.dropped, "buffered": len(self._buf)})})
            r.expire(key, HEARTBEAT_TTL_S)
        except Exception:
            pass


_recorder = Recorder()


def get_recorder() -> Recorder:
    return _recorder


def record(*args, **kwargs) -> None:
    _recorder.record(*args, **kwargs)
