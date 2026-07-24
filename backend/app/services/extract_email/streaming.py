"""Turn an extraction coroutine into a live Server-Sent-Events stream.

`sse_events(run)` runs `run()` (which performs a normal extraction) inside a
context that has a ProgressSink installed, and yields one SSE frame per
progress event the pipeline emits — then a final `done` frame carrying the
result. The pipeline code itself is unchanged; it just calls progress.emit(),
which is a no-op on the non-streamed paths.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Awaitable, Callable

from app.services.extract_email.progress import ProgressSink, reset_sink, set_sink


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def sse_events(run: Callable[[], Awaitable[dict]]) -> AsyncIterator[str]:
    """`run` returns a JSON-serialisable result dict; it is emitted in the
    final `done` event so the client can continue the normal flow (open
    Compare & Fix for staged items, etc.)."""
    sink = ProgressSink()

    async def runner() -> None:
        token = set_sink(sink)
        try:
            result = await run()
            sink.emit("done", "ok", "Finished.", result=result)
        except Exception as exc:  # surface the failure to the client, don't hang
            sink.emit("error", "fail", str(exc)[:400])
        finally:
            reset_sink(token)
            sink.close()

    task = asyncio.create_task(runner())
    # Kick-off frame so the client shows the panel instantly.
    yield _frame({"stage": "start", "status": "start", "message": "Starting…",
                  "llm_calls": 0, "elapsed_ms": 0, "data": {}})
    try:
        while True:
            event = await sink.queue.get()
            if event is None:       # sentinel from sink.close()
                break
            yield _frame(event)
    finally:
        if not task.done():
            task.cancel()
