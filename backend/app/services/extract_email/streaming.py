"""Turn an extraction coroutine into a live Server-Sent-Events stream.

`sse_events(run)` runs `run()` (which performs a normal extraction) inside a
context that has a ProgressSink installed, and yields one SSE frame per
progress event the pipeline emits — then a final `done` frame carrying the
result. The pipeline code itself is unchanged; it just calls progress.emit(),
which is a no-op on the non-streamed paths.

The extraction itself runs as its OWN task (`runner`), decoupled from this
generator's iteration on purpose: navigating away / closing the tab tears
down the SSE connection (this generator), but must NOT abort the extraction
already in flight — that's a paid LLM call and a database write in progress,
and killing it mid-way is worse than just losing the live progress feed
(partial DB state is a bigger problem than a UI that stopped watching).
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Awaitable, Callable

from app.services.extract_email.progress import ProgressSink, reset_sink, set_sink


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


# asyncio only holds a WEAK reference to a task created with create_task() —
# with nothing else referencing it, the task can be garbage-collected (and
# silently dropped) before it finishes. A strong reference here, discarded
# via the done-callback once the task actually completes, keeps every
# in-flight extraction alive for its own duration regardless of whether the
# request that started it is still around.
_background_tasks: set[asyncio.Task] = set()


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

    # Not awaited here on purpose — see module docstring. This task keeps
    # running even after this generator is torn down (client disconnect),
    # so extraction always finishes and stages/files its result regardless
    # of whether anyone is still watching the live progress feed.
    task = asyncio.create_task(runner())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    # Kick-off frame so the client shows the panel instantly.
    yield _frame({"stage": "start", "status": "start", "message": "Starting…",
                  "llm_calls": 0, "elapsed_ms": 0, "data": {}})
    while True:
        event = await sink.queue.get()
        if event is None:       # sentinel from sink.close()
            break
        yield _frame(event)
