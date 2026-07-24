"""Live progress reporting for the extraction pipeline.

The pipeline (collector -> analyser -> grouping -> staging) is shared by every
entry point (Extract Email, Upload, chat upload). To let the UI watch a run
happen in real time WITHOUT threading a callback through every function, a
`ProgressSink` is put on a context variable for the duration of a streamed
run. Pipeline code calls the module-level `emit(...)` / `count_llm()` helpers,
which are cheap no-ops when nothing is streaming — so the normal (non-streamed)
path is completely unaffected.

Event shape (one JSON object per Server-Sent-Event frame):
  { stage, status, message, llm_calls, elapsed_ms, data }
    stage   : unpack | format | extract | approval | group | autoaccept | file
              | done | error
    status  : start | spin | ok | warn | fail
    data    : optional per-stage payload (sheet names, reasons, result, ...)
"""
from __future__ import annotations

import asyncio
import contextvars
import time


class ProgressSink:
    """A queue of progress events for one streamed extraction run."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self._started = time.monotonic()
        self.llm_calls = 0

    def emit(self, stage: str, status: str, message: str, **data) -> None:
        self.queue.put_nowait({
            "stage": stage,
            "status": status,
            "message": message,
            "llm_calls": self.llm_calls,
            "elapsed_ms": int((time.monotonic() - self._started) * 1000),
            "data": data or {},
        })

    def count_llm(self) -> int:
        self.llm_calls += 1
        return self.llm_calls

    def close(self) -> None:
        # Sentinel — the SSE generator stops when it sees None.
        self.queue.put_nowait(None)


_SINK: contextvars.ContextVar[ProgressSink | None] = contextvars.ContextVar(
    "extract_progress_sink", default=None)


def set_sink(sink: ProgressSink | None):
    return _SINK.set(sink)


def reset_sink(token) -> None:
    _SINK.reset(token)


def emit(stage: str, status: str, message: str, **data) -> None:
    sink = _SINK.get()
    if sink is not None:
        sink.emit(stage, status, message, **data)


def count_llm() -> int:
    """Bump the running LLM-call counter for the active stream (else 0)."""
    sink = _SINK.get()
    return sink.count_llm() if sink is not None else 0
