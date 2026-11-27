"""Read side of the metrics: aggregates the per-minute Redis hashes written by recorder.py
into one snapshot for the Monitor page, plus live system state and alerts."""
from __future__ import annotations

import json
import shutil
import time
from typing import Any

from app.services.monitoring.recorder import (
    EVENTS_KEY, LATENCY_BUCKETS_MS, percentile_from_buckets, redis_client,
)

N_BUCKETS = len(LATENCY_BUCKETS_MS) + 1


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _minute_rows(minutes: int) -> dict[int, dict[str, dict[str, float]]]:
    r = redis_client()
    if r is None:
        return {}
    now_min = int(time.time() // 60)
    window = list(range(now_min - minutes + 1, now_min + 1))
    pipe = r.pipeline(transaction=False)
    for m in window:
        pipe.smembers(f"mon:idx:{m}")
    members = pipe.execute()
    pipe = r.pipeline(transaction=False)
    order = []
    for m, mem in zip(window, members):
        for member in mem or []:
            member = _s(member)
            pipe.hgetall(f"mon:m:{m}:{member}")
            order.append((m, member))
    out: dict[int, dict[str, dict[str, float]]] = {}
    for (m, member), h in zip(order, pipe.execute() if order else []):
        row = {_s(k): _num(v) for k, v in (h or {}).items()}
        if row:
            out.setdefault(m, {})[member] = row
    return out


def _buckets(row: dict[str, float]) -> list[int]:
    return [int(row.get(f"b{i}", 0)) for i in range(N_BUCKETS)]


def _merge(into: dict[str, float], row: dict[str, float]) -> None:
    for k, v in row.items():
        into[k] = into.get(k, 0.0) + v


def _shape(kind: str, role: str, model: str, agg: dict[str, float]) -> dict[str, Any]:
    calls = int(agg.get("calls", 0))
    buckets = _buckets(agg)
    return {
        "kind": kind, "role": role, "model": model, "calls": calls, "errors": int(agg.get("errors", 0)),
        "avg_ms": round(agg.get("total_ms", 0.0) / calls, 1) if calls else None,
        "p50_ms": percentile_from_buckets(buckets, 0.5), "p95_ms": percentile_from_buckets(buckets, 0.95),
        "tokens_in": int(agg.get("tokens_in", 0)), "tokens_out": int(agg.get("tokens_out", 0)),
        "cost_usd": round(agg.get("cost", 0.0), 4),
    }


def _grouped(data, kind: str, by_model: bool = True) -> list[dict[str, Any]]:
    acc: dict[str, dict[str, float]] = {}
    for rows in data.values():
        for member, row in rows.items():
            k, role, model = member.split("|", 2)
            if k != kind:
                continue
            _merge(acc.setdefault(f"{k}|{role}|{model if by_model else '*'}", {}), row)
    return sorted((_shape(*m.split("|", 2), a) for m, a in acc.items()), key=lambda x: -x["calls"])


def _totals(data, kind: str) -> dict[str, Any]:
    acc: dict[str, float] = {}
    for rows in data.values():
        for member, row in rows.items():
            if member.startswith(kind + "|"):
                _merge(acc, row)
    return _shape(kind, "*", "*", acc)


def _series(data, minutes: int, kind: str, points: int = 60) -> list[dict[str, Any]]:
    step = max(1, -(-minutes // points))
    now_min = int(time.time() // 60)
    out = []
    for start in range(now_min - minutes + 1, now_min + 1, step):
        acc: dict[str, float] = {}
        for m in range(start, min(start + step, now_min + 1)):
            for member, row in data.get(m, {}).items():
                if member.startswith(kind + "|"):
                    _merge(acc, row)
        calls = int(acc.get("calls", 0))
        out.append({"t": start * 60, "calls": calls, "errors": int(acc.get("errors", 0)),
                    "p95_ms": percentile_from_buckets(_buckets(acc), 0.95) if calls else None,
                    "cost_usd": round(acc.get("cost", 0.0), 4)})
    return out


def processes() -> list[dict[str, Any]]:
    r = redis_client()
    if r is None:
        return []
    out = []
    for key in r.scan_iter(match="mon:proc:*", count=200):
        h = {_s(k): _s(v) for k, v in (r.hgetall(key) or {}).items()}
        if not h:
            continue
        try:
            stats = json.loads(h.get("stats") or "{}")
        except ValueError:
            stats = {}
        age = time.time() - _num(h.get("beat"))
        out.append({"id": _s(key).split("mon:proc:", 1)[1], "kind": h.get("kind"), "host": h.get("host"),
                    "pid": int(_num(h.get("pid"))), "uptime_s": round(time.time() - _num(h.get("started_at"))),
                    "age_s": round(age, 1), "online": age < 20, "stats": stats})
    return sorted(out, key=lambda p: (p["kind"] or "", p["host"] or "", p["pid"]))


def events(*, kinds: tuple | None = None, failures_only: bool = False, limit: int = 40) -> list[dict[str, Any]]:
    r = redis_client()
    if r is None:
        return []
    out = []
    for raw in r.lrange(EVENTS_KEY, 0, 1999):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if kinds and e.get("kind") not in kinds:
            continue
        if failures_only and e.get("status") == "ok":
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def queues() -> dict[str, Any]:
    """Celery's Redis broker: one list per queue; tasks a worker holds but has not finished
    sit in the 'unacked' hash."""
    r = redis_client()
    if r is None:
        return {"available": False}
    try:
        return {"available": True, "celery": int(r.llen("celery")), "extract": int(r.llen("extract")),
                "in_progress": int(r.hlen("unacked"))}
    except Exception as e:
        return {"available": False, "error": str(e)[:200]}


def redis_info() -> dict[str, Any]:
    r = redis_client()
    if r is None:
        return {"available": False}
    try:
        mem, stats, server = r.info("memory"), r.info("stats"), r.info("server")
        return {"available": True, "used_mb": round(mem.get("used_memory", 0) / 1048576, 1),
                "max_mb": round(mem.get("maxmemory", 0) / 1048576, 1) or None,
                "policy": mem.get("maxmemory_policy"), "evicted_keys": stats.get("evicted_keys", 0),
                "uptime_h": round(server.get("uptime_in_seconds", 0) / 3600, 1)}
    except Exception as e:
        return {"available": False, "error": str(e)[:200]}


def disk() -> dict[str, Any]:
    """The disk the containers (and their logs) live on."""
    try:
        u = shutil.disk_usage("/")
        return {"total_gb": round(u.total / 1e9, 1), "free_gb": round(u.free / 1e9, 1),
                "used_pct": round(100 * u.used / u.total, 1)}
    except Exception:
        return {}


def snapshot(minutes: int) -> dict[str, Any]:
    data = _minute_rows(minutes)
    return {
        "window_minutes": minutes,
        "llm": {"totals": _totals(data, "llm"), "by_step": _grouped(data, "llm"),
                "series": _series(data, minutes, "llm")},
        "graph": {"totals": _totals(data, "graph"), "by_call": _grouped(data, "graph", by_model=False)},
        "jobs": {"totals": _totals(data, "job"), "by_task": _grouped(data, "job", by_model=False)},
        "api": {"totals": _totals(data, "api"),
                "slowest": sorted(_grouped(data, "api", by_model=False),
                                  key=lambda x: -(x["p95_ms"] or 0))[:10],
                "errors": [x for x in _grouped(data, "api", by_model=False) if x["errors"]][:10]},
        "processes": processes(),
        "recent_llm": events(kinds=("llm",), limit=40),
        "failures": events(failures_only=True, limit=40),
        "queues": queues(),
        "redis": redis_info(),
        "disk": disk(),
    }


def alerts(snap: dict[str, Any], *, sync: dict, auto: dict) -> list[dict[str, str]]:
    """Plain-language problems worth a look, worst first."""
    out: list[dict[str, str]] = []

    def add(level: str, text: str) -> None:
        out.append({"level": level, "text": text})

    kinds_online = {p["kind"] for p in snap["processes"] if p["online"]}
    if snap["processes"]:
        if "worker-extract" not in kinds_online:
            add("critical", "No extraction worker is running - extraction tasks will wait in the queue.")
        if "worker" not in kinds_online:
            add("critical", "The background worker is not running - mail sync and scheduled jobs are stopped.")
    if sync.get("sync_error"):
        add("critical", f"Mail sync is failing: {sync['sync_error']}")
    elif sync.get("last_sync_at"):
        from datetime import datetime, timezone
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(sync["last_sync_at"])).total_seconds()
            if age > 30 * 60:
                add("serious", f"The mailbox has not synced for {int(age // 60)} minutes.")
        except ValueError:
            pass
    llm = snap["llm"]["totals"]
    if llm["calls"] >= 5 and llm["errors"] / llm["calls"] > 0.2:
        add("serious", f"{llm['errors']} of {llm['calls']} LLM calls failed in this window.")
    q = snap["queues"]
    if q.get("available") and q.get("extract", 0) > 200:
        add("warning", f"{q['extract']} threads are waiting for extraction.")
    rd = snap["redis"]
    if rd.get("available") and rd.get("evicted_keys"):
        add("warning", f"Redis has evicted {rd['evicted_keys']} cached keys (memory pressure).")
    if rd.get("available") and rd.get("max_mb") and rd["used_mb"] > 0.85 * rd["max_mb"]:
        add("warning", f"Redis memory at {rd['used_mb']} of {rd['max_mb']} MB.")
    dk = snap["disk"]
    if dk.get("used_pct", 0) > 85:
        add("serious" if dk["used_pct"] > 93 else "warning",
            f"Server disk is {dk['used_pct']}% full ({dk['free_gb']} GB free).")
    if auto.get("failed"):
        add("warning", f"Auto Extract: {auto['failed']} thread(s) failed in the current run"
            + (f" - last: {auto['last_error']}" if auto.get("last_error") else "") + ".")
    order = {"critical": 0, "serious": 1, "warning": 2}
    return sorted(out, key=lambda a: order[a["level"]])
