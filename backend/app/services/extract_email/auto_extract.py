"""Background bulk Extract Email — runs every thread in the inbox one at a
time instead of N manual button clicks, in a Celery worker so it survives
independently of any browser tab: navigate anywhere in the app, close the
tab, come back later — the run (and its status) live server-side.

Status is kept in the shared cache (Redis in Docker; in-memory fallback if
Redis is unreachable) under one key, polled by the UI. Stop is cooperative:
it asks the loop to stop AFTER the thread currently in flight finishes — a
clean stop point between threads, never a mid-extraction kill that could
leave a half-written record.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.models.email_message import EmailMessage, EmailStatus

_STATUS_KEY = "auto_extract:status"
_STOP_KEY = "auto_extract:stop"
# Refreshed on every update while running; just a safety net so a status blob
# never lingers forever if a worker dies mid-run without cleaning up.
_STATUS_TTL = 6 * 3600

_IDLE: dict = {
    "state": "idle",           # idle | running | stopping | stopped | completed
    "total": 0,
    "processed": 0,
    "succeeded": 0,
    "failed": 0,
    "current": None,           # {"thread_id": provider_message_id, "subject": str}
    "started_at": None,
    "finished_at": None,
    "last_error": None,
}


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def get_status() -> dict:
    return await cache.get(_STATUS_KEY) or dict(_IDLE)


async def _set_status(status: dict) -> None:
    await cache.set(_STATUS_KEY, status, ttl=_STATUS_TTL)


async def _update_status(**changes) -> dict:
    status = await get_status()
    status.update(changes)
    await _set_status(status)
    return status


async def request_stop() -> dict:
    """Ask a running job to stop once its current thread finishes."""
    status = await get_status()
    if status.get("state") == "running":
        await cache.set(_STOP_KEY, True, ttl=_STATUS_TTL)
        status = await _update_status(state="stopping")
    return status


async def _stop_requested() -> bool:
    return bool(await cache.get(_STOP_KEY))


async def _list_all_thread_anchors(db: AsyncSession) -> list[tuple[str, str]]:
    """(provider_message_id, subject) for the newest message of every thread,
    newest-first — one row per Outlook-style conversation, the same grouping
    GET /inbox/threads uses, but every thread and no page limit. Archived
    threads are excluded (archiving is an explicit "not this one")."""
    thread_key = func.coalesce(EmailMessage.conversation_id, EmailMessage.id)
    stmt = (
        select(EmailMessage)
        .where(EmailMessage.status != EmailStatus.ARCHIVED)
        .distinct(thread_key)
        .order_by(thread_key, EmailMessage.received_at.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    rows.sort(key=lambda r: r.received_at or epoch, reverse=True)
    return [(r.provider_message_id, r.subject or "(no subject)") for r in rows]


async def start() -> dict:
    """Kick off the background run. Idempotent — if one is already running or
    winding down, returns its current status instead of starting a second,
    overlapping run."""
    status = await get_status()
    if status.get("state") in ("running", "stopping"):
        return status
    await cache.delete(_STOP_KEY)
    from app.services.tasks import auto_extract_all_task

    running = {**_IDLE, "state": "running", "started_at": _now_iso()}
    await _set_status(running)
    auto_extract_all_task.delay()
    return running


async def run_auto_extract() -> dict:
    """The actual loop — runs inside the Celery task. A fresh DB session per
    thread, so one slow or failed extraction never blocks or poisons the
    next one's transaction."""
    from app.core import datacache
    from app.core.database import SessionLocal
    from app.services.agents.full_email_extract import extract_full_email
    from app.services.extract_email.thread_scope import prior_message_for_merge

    async with SessionLocal() as db:
        anchors = await _list_all_thread_anchors(db)

    # started_at was already set by start(); keep it if present so the
    # displayed run duration is from the moment the button was pressed, not
    # from when this task actually got a worker slot.
    started_at = (await get_status()).get("started_at") or _now_iso()
    await _update_status(
        state="running", total=len(anchors), processed=0, succeeded=0,
        failed=0, current=None, started_at=started_at, finished_at=None,
    )

    succeeded = failed = processed = 0
    for pmid, subject in anchors:
        if await _stop_requested():
            await _update_status(state="stopped", current=None, finished_at=_now_iso())
            await cache.delete(_STOP_KEY)
            return await get_status()

        await _update_status(current={"thread_id": pmid, "subject": subject})
        async with SessionLocal() as db:
            row = (await db.execute(
                select(EmailMessage).where(EmailMessage.provider_message_id == pmid)
            )).scalar_one_or_none()
            if row is None:
                failed += 1
            else:
                try:
                    prior = await prior_message_for_merge(db, row)
                    await extract_full_email(db, row, prior_email=prior)
                    succeeded += 1
                except Exception as e:
                    failed += 1
                    await _update_status(last_error=f"{subject}: {str(e)[:180]}")
                await datacache.bust_pipeline()
        processed += 1
        await _update_status(processed=processed, succeeded=succeeded, failed=failed)

    return await _update_status(state="completed", current=None, finished_at=_now_iso())
