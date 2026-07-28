"""sse_events — the SSE progress stream for Extract Email's streamed endpoint.

Real case: a user clicks Extract Email, then navigates to another page (or
just closes the tab) while it's still running. Starlette tears down the
response's async generator the moment the client disconnects. The
extraction itself must NOT be cancelled by that — it's a paid LLM call and
a database write in progress; losing the live progress feed is fine, losing
the work (or worse, leaving a half-written DB row) is not.
"""
import asyncio

from app.services.extract_email.streaming import sse_events


async def test_extraction_survives_the_client_disconnecting_early():
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_run() -> dict:
        started.set()
        await asyncio.sleep(0.2)
        finished.set()
        return {"ok": True}

    gen = sse_events(slow_run)
    await gen.__anext__()          # the "start" kick-off frame
    await asyncio.wait_for(started.wait(), timeout=1.0)

    # Simulate the client disconnecting — Starlette closes the generator.
    await gen.aclose()

    # The extraction must still run to completion in the background.
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    assert finished.is_set()


async def test_a_normal_full_stream_still_yields_every_frame_then_done():
    """The fix must not break the ordinary (nobody disconnects) path."""
    async def run() -> dict:
        from app.services.extract_email.progress import emit
        emit("unpack", "ok", "done unpacking")
        return {"ok": True}

    frames = [frame async for frame in sse_events(run)]
    assert len(frames) >= 3   # start, unpack, done
    assert '"stage": "start"' in frames[0]
    assert '"stage": "unpack"' in frames[1]
    assert '"stage": "done"' in frames[-1]
    assert '"result": {"ok": true}' in frames[-1]


async def test_a_run_that_raises_still_emits_an_error_frame_and_closes():
    async def failing_run() -> dict:
        raise ValueError("boom")

    frames = [frame async for frame in sse_events(failing_run)]
    assert any('"stage": "error"' in f and "boom" in f for f in frames)
