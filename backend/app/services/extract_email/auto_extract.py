"""Background bulk Extract Email — runs every thread in the inbox one at a
time instead of N manual button clicks, in a Celery worker so it survives
independently of any browser tab: navigate anywhere in the app, close the
tab, come back later — the run (and its status) live server-side.

Status is kept in the shared cache (Redis in Docker; in-memory fallback if
Redis is unreachable) under one key, polled by the UI. Stop is cooperative:
it asks the loop to stop AFTER the thread currently in flight finishes — a
clean stop point between threads, never a mid-extraction kill that could
leave a half-written record.

Turning it on is not just "run once": it also flips a separate, long-lived
`enabled` flag (own Redis key, own TTL — independent of any one run's status
blob). While enabled, app.services.tasks.sync_inbox_task re-triggers a run
every time its ~60s background sync pulls in mail the mailbox didn't have
before — so newly arrived mail gets extracted on its own, with nobody having
to click Auto Extract again. Each re-trigger is cheap even across hundreds of
already-done threads: already-extracted ones are counted in one shot (no
model call), then genuinely-new mail is extracted newest-first. Turning
it off (Stop) clears `enabled` too, so a later sync tick won't quietly turn
it back on.
"""
from __future__ import annotations

import asyncio
import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.models.email_message import EmailMessage, EmailStatus
from app.models.pipeline_file import PipelineFile
from app.services.extract_email.constants import TAG_PREFIX

_STATUS_KEY = "auto_extract:status"
_STOP_KEY = "auto_extract:stop"
# Long-lived on/off switch — separate from _STATUS_KEY, which only describes
# the CURRENT (or most recent) run. This is "should a background sync tick
# start a new run on its own", and outlives any single run's completion.
_ENABLED_KEY = "auto_extract:enabled"
_ENABLED_TTL = 30 * 24 * 3600  # 30 days — a deliberate mode, not run bookkeeping
# Refreshed on every update while running; just a safety net so a status blob
# never lingers forever if a worker dies mid-run without cleaning up.
_STATUS_TTL = 6 * 3600

_IDLE: dict = {
    "state": "idle",           # idle | running | stopping | stopped | completed
    "total": 0,
    "processed": 0,
    "succeeded": 0,
    "failed": 0,
    # Already extracted, nothing new since — no Graph call, no model call,
    # not counted as work done, just correctly recognised as already done.
    "skipped": 0,
    "current": None,           # {"thread_id": provider_message_id, "subject": str}
    "started_at": None,
    "finished_at": None,
    "last_error": None,
}


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def is_enabled() -> bool:
    """Whether Auto Extract's watch-for-new-mail mode is on — checked by
    sync_inbox_task before it re-triggers a run off the back of a background
    sync. Independent of whether a run happens to be in flight right now."""
    return bool(await cache.get(_ENABLED_KEY))


async def _set_enabled(value: bool) -> None:
    if value:
        await cache.set(_ENABLED_KEY, True, ttl=_ENABLED_TTL)
    else:
        await cache.delete(_ENABLED_KEY)


async def get_status() -> dict:
    status = dict(await cache.get(_STATUS_KEY) or _IDLE)
    status["enabled"] = await is_enabled()
    return status


async def _set_status(status: dict) -> None:
    await cache.set(_STATUS_KEY, status, ttl=_STATUS_TTL)


async def _update_status(**changes) -> dict:
    status = await get_status()
    status.update(changes)
    await _set_status(status)
    return status


async def request_stop() -> dict:
    """Turn Auto Extract off: ask a running job to stop once its current
    thread finishes, AND clear the persistent `enabled` flag so a later
    background sync tick doesn't silently start it running again."""
    await _set_enabled(False)
    status = await get_status()
    if status.get("state") == "running":
        await cache.set(_STOP_KEY, True, ttl=_STATUS_TTL)
        status = await _update_status(state="stopping")
    return status


async def _stop_requested() -> bool:
    return bool(await cache.get(_STOP_KEY))


async def _list_all_thread_anchors(db: AsyncSession) -> list[tuple[str, str, str, dt.datetime | None]]:
    """(provider_message_id, subject, thread_key, newest received_at) for
    every thread, newest-first — one row per Outlook-style conversation, the
    same grouping GET /inbox/threads uses, but every thread and no page
    limit. Archived threads are excluded (archiving is an explicit "not this
    one").

    `thread_key` is the SAME key sheet_cache.last_extraction_at() reads
    (conversation_id, or this message's own id for a singleton thread) —
    the caller uses it to recognise "nothing new since the last real
    extraction" before touching the mailbox or the model at all.
    """
    group_key = func.coalesce(EmailMessage.conversation_id, EmailMessage.id)
    stmt = (
        select(EmailMessage)
        .where(EmailMessage.status != EmailStatus.ARCHIVED)
        .distinct(group_key)
        .order_by(group_key, EmailMessage.received_at.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    rows.sort(key=lambda r: r.received_at or epoch, reverse=True)
    return [
        (r.provider_message_id, r.subject or "(no subject)",
         r.conversation_id or r.provider_message_id, r.received_at)
        for r in rows
    ]


async def coverage(db: AsyncSession) -> dict:
    """How many threads, mailbox-wide, show the Extracted badge right now —
    computed live off the same PipelineFile tag the Inbox's own per-thread
    badge and the skip check above both read, not a separately-maintained
    counter that could drift from what's actually displayed. Backs the
    small "Details" panel next to the Auto Extract button.

    thread_key mirrors what's actually STORED on PipelineFile.thread_key —
    conversation_id, or this message's own provider_message_id for a
    singleton thread (NOT its internal db `id`; see _list_all_thread_anchors
    above, which uses `id` only as a SQL-level dedup tiebreaker, never as
    the thread_key value itself)."""
    thread_key = func.coalesce(EmailMessage.conversation_id, EmailMessage.provider_message_id)
    total = (await db.execute(
        select(func.count(func.distinct(thread_key)))
    )).scalar_one()
    extracted = (await db.execute(
        select(func.count(func.distinct(thread_key))).where(
            select(PipelineFile.id).where(
                PipelineFile.source_kind == "email",
                PipelineFile.thread_key == thread_key,
                PipelineFile.attachment_id.like(f"{TAG_PREFIX}%"),
            ).exists()
        )
    )).scalar_one()
    return {"extracted_threads": extracted, "total_threads": total}


async def start() -> dict:
    """Kick off the background run, and turn on the persistent watch-for-new-
    mail mode (see module docstring) so this keeps happening on its own as
    mail arrives, not just this one time. Idempotent — if one is already
    running or winding down, returns its current status instead of starting
    a second, overlapping run (still leaves `enabled` on either way)."""
    await _set_enabled(True)
    status = await get_status()
    if status.get("state") in ("running", "stopping"):
        return status
    await cache.delete(_STOP_KEY)
    from app.services.tasks import auto_extract_all_task

    running = {**_IDLE, "state": "running", "started_at": _now_iso(), "enabled": True}
    await _set_status(running)
    auto_extract_all_task.delay()
    return running


async def _extract_with_live_progress(db, row: EmailMessage, subject: str, prior) -> None:
    """Run extract_full_email through the SAME ProgressSink mechanism the
    manual "Extract Email" SSE stream uses (see streaming.py/progress.py) —
    but instead of yielding each frame to an HTTP response, mirror it into
    this thread's `current.events` in the shared Redis status blob. A
    browser polling /auto-extract/status then sees the identical live
    unpack/pass1/pass2/batch animation Extract Email shows when run by
    hand, not just a coarse per-thread count. The pipeline code itself
    needs no changes — progress.emit() already no-ops unless a sink is
    installed for the current context, exactly like the SSE path."""
    from app.services.agents.full_email_extract import extract_full_email
    from app.services.extract_email.progress import ProgressSink, reset_sink, set_sink

    sink = ProgressSink()
    events: list[dict] = []

    async def _drain() -> None:
        while True:
            event = await sink.queue.get()
            if event is None:
                break
            events.append(event)
            await _update_status(current={
                "thread_id": row.provider_message_id, "subject": subject, "events": events,
            })

    token = set_sink(sink)
    drain_task = asyncio.create_task(_drain())
    try:
        await extract_full_email(db, row, prior_email=prior)
    finally:
        reset_sink(token)
        sink.close()
        await drain_task


async def run_auto_extract() -> dict:
    """The actual loop — runs inside the Celery task. A fresh DB session per
    thread, so one slow or failed extraction never blocks or poisons the
    next one's transaction."""
    from app.core import datacache
    from app.core.database import SessionLocal
    from app.services.extract_email import sheet_cache
    from app.services.extract_email.thread_scope import prior_message_for_merge

    async with SessionLocal() as db:
        anchors = await _list_all_thread_anchors(db)
        last_at_by_thread = await sheet_cache.last_extraction_at_bulk(
            db, [thread_key for _, _, thread_key, _ in anchors])

    # Already-extracted threads (nothing newer than last extract) are counted
    # in ONE shot — no per-thread loop, no Graph, no model. Then only the
    # remaining ones run, newest-first, so a watch-mode tick on 1 new mail
    # in a 500-thread mailbox jumps straight to that mail instead of walking
    # 414…470 of already-done counters first.
    skip_now, need_real = [], []
    for anchor in anchors:
        _, _, thread_key, newest_at = anchor
        last_at = last_at_by_thread.get(thread_key)
        if last_at is not None and newest_at is not None and last_at >= newest_at:
            skip_now.append(anchor)
        else:
            need_real.append(anchor)

    # started_at was already set by start(); keep it if present so the
    # displayed run duration is from the moment the button was pressed, not
    # from when this task actually got a worker slot.
    started_at = (await get_status()).get("started_at") or _now_iso()
    skipped = len(skip_now)
    processed = skipped
    succeeded = failed = 0
    await _update_status(
        state="running", total=len(anchors), processed=processed, succeeded=0,
        failed=0, skipped=skipped, current=None, started_at=started_at,
        finished_at=None,
    )

    for pmid, subject, _thread_key, _newest_at in need_real:
        if await _stop_requested():
            await _update_status(state="stopped", current=None, finished_at=_now_iso())
            await cache.delete(_STOP_KEY)
            return await get_status()

        async with SessionLocal() as db:
            await _update_status(current={"thread_id": pmid, "subject": subject, "events": []})
            row = (await db.execute(
                select(EmailMessage).where(EmailMessage.provider_message_id == pmid)
            )).scalar_one_or_none()
            if row is None:
                failed += 1
            else:
                try:
                    prior = await prior_message_for_merge(db, row)
                    await _extract_with_live_progress(db, row, subject, prior)
                    succeeded += 1
                except Exception as e:
                    failed += 1
                    await _update_status(last_error=f"{subject}: {str(e)[:180]}")
                await datacache.bust_pipeline()
        processed += 1
        # Clear `current` the instant this thread is done — otherwise it stays
        # set to the just-finished thread (already counted in `processed`)
        # until the next one starts, so the UI would show a stale "processing
        # X" for a thread that's actually already been counted as done.
        await _update_status(
            processed=processed, succeeded=succeeded, failed=failed,
            skipped=skipped, current=None)

    return await _update_status(state="completed", current=None, finished_at=_now_iso())
