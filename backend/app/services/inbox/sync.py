"""Inbox provider sync — upsert Graph/mock messages into EmailMessage, and a
throttled incremental pull that keeps the local mirror fresh.

Used from TWO places:
  - on-demand, inside GET /inbox and /inbox/threads (so opening the page
    never serves data older than the throttle window);
  - a Celery beat job (app.services.tasks.sync_inbox_task) that runs this on
    a fixed schedule REGARDLESS of whether anyone has the Inbox page open —
    so new mail shows up without a user action being the trigger for it.
Both paths hit the exact same throttle/lock, so they never duplicate work.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.core.config import settings
from app.models.email_message import EmailMessage, EmailStatus
from app.services.email_provider import get_email_provider

SYNC_LOCK_KEY = "inbox:sync:lock"
SYNC_FRESH_KEY = "inbox:sync:fresh"
SYNC_LAST_KEY = "inbox:sync:last"   # epoch seconds of the last successful sync
# Re-fetch window overlap: clock skew / out-of-order receivedDateTime; the
# upsert dedupes anything fetched twice.
SYNC_OVERLAP = timedelta(minutes=10)


async def sync_message(db: AsyncSession, msg) -> EmailMessage:
    """Upsert a provider message into EmailMessage.

    Must be concurrency-safe: multiple requests/jobs may sync the same
    provider id at the same time (open inbox, click AI check, the beat job,
    etc.). Use a single INSERT .. ON CONFLICT .. DO UPDATE to avoid unique
    violations.
    """
    atts = [
        {"attachment_id": a.attachment_id, "filename": a.filename,
         "content_type": a.content_type, "size": a.size, "kind": a.kind, "cid": a.cid,
         "is_inline": a.is_inline}
        for a in msg.attachments
    ]
    has_approval = any(a["kind"] == "approval_screenshot" for a in atts)

    insert_stmt = pg_insert(EmailMessage).values(
        provider_message_id=msg.message_id,
        conversation_id=msg.conversation_id,
        sender_name=msg.sender_name,
        sender_email=msg.sender_email,
        to_recipients=msg.to_recipients or [],
        cc_recipients=msg.cc_recipients or [],
        subject=msg.subject,
        received_at=msg.received_at,
        body_text=msg.body_text,
        body_html=msg.body_html,
        attachments=atts,
        has_approval_screenshot=has_approval,
        status=EmailStatus.NEW,
    )
    stmt = (
        insert_stmt
        .on_conflict_do_update(
            index_elements=["provider_message_id"],
            set_={
                # Preserve workflow fields (status/decided_at). Only refresh message data.
                # COALESCE: never let a resync null out a previously-known
                # conversation_id if this particular call somehow has none.
                "conversation_id": func.coalesce(
                    insert_stmt.excluded.conversation_id, EmailMessage.conversation_id),
                "sender_name": msg.sender_name,
                "sender_email": msg.sender_email,
                "to_recipients": msg.to_recipients or [],
                "cc_recipients": msg.cc_recipients or [],
                "subject": msg.subject,
                "received_at": msg.received_at,
                "body_text": msg.body_text,
                "body_html": func.coalesce(
                    insert_stmt.excluded.body_html, EmailMessage.body_html),
                "attachments": atts,
                "has_approval_screenshot": has_approval,
            },
        )
        .returning(EmailMessage.id)
    )
    await db.execute(stmt)
    row = (
        await db.execute(
            select(EmailMessage).where(EmailMessage.provider_message_id == msg.message_id)
        )
    ).scalar_one()
    return row


async def _has_any_email(db: AsyncSession) -> bool:
    return (
        await db.execute(select(func.count()).select_from(EmailMessage))
    ).scalar_one() > 0


async def _pages(provider, since, before):
    """Pages from the provider; a provider without paging (older/mock/duck-typed)
    yields its whole list_messages() result as one page."""
    paged = getattr(provider, "iter_message_pages", None)
    if paged is None:
        yield await provider.list_messages(None, since=since)
        return
    async for page in paged(since=since, before=before):
        yield page


def _light(row):
    """What the caller needs from a synced row (ids + subject/time), without the
    message body - a first-time backfill can be hundreds of thousands of rows."""
    from types import SimpleNamespace

    if not hasattr(row, "provider_message_id"):
        return row
    return SimpleNamespace(
        provider_message_id=row.provider_message_id,
        conversation_id=getattr(row, "conversation_id", None),
        subject=getattr(row, "subject", None), received_at=getattr(row, "received_at", None))


SYNC_BACKFILL_KEY = "inbox:sync:backfill"   # {"before": iso, "started_at": epoch} while a first full crawl is unfinished
SYNC_ERROR_KEY = "inbox:sync:last_error"
_LOCK_TTL = 120          # refreshed after every page, so a long backfill keeps it


async def sync_inbox(db: AsyncSession) -> list[EmailMessage] | None:
    """Throttled, incremental provider sync. Never blocks the caller on a full
    mailbox download:

    - fresh (synced < INBOX_SYNC_MIN_INTERVAL_SECONDS ago) -> no-op, serve DB;
    - otherwise ask the provider only for messages received after the LAST
      SUCCESSFUL SYNC (one small request). A full folder crawl happens only
      when there is no sync marker (first boot / cache flushed) OR the table
      is empty despite a marker existing (Postgres was wiped/restored without
      also clearing the Redis cursor - trust the DB's actual content over a
      stale cursor rather than silently syncing "nothing new" forever);
    - any provider/cache error -> serve existing DB rows, never raise - but the
      error is LOGGED and kept in the cache (SYNC_ERROR_KEY), not swallowed.

    The pull is CHECKPOINTED page by page (no message-count cap): each page of
    messages is upserted and committed on its own, and the "last successful
    sync" cursor only advances once EVERY page has been stored. A failure at
    page 300 of a first crawl therefore loses one page, not the crawl - the next
    run resumes below the oldest message already stored (SYNC_BACKFILL_KEY).

    Returns the upserted EmailMessage rows (so IDs/conversation_id are
    populated; a first-time backfill returns light stand-ins carrying just the
    ids/subject/time), or None if this call was skipped (still fresh, or
    another sync already in flight) or hit an error.
    `sync_inbox_task` uses this list to enqueue extraction for EXACTLY these
    threads - no point re-triggering Auto Extract on a tick that changed
    nothing, and no need to rescan the whole mailbox to find what changed
    when the sync itself already knows precisely which rows are new.
    """
    import logging
    import uuid

    log = logging.getLogger("inbox.sync")
    token = uuid.uuid4().hex
    try:
        if await cache.exists(SYNC_FRESH_KEY):
            return None
        # Atomic acquire (the old exists()+set() pair let two syncs both pass).
        if not await cache.acquire_lock(SYNC_LOCK_KEY, _LOCK_TTL, token):
            return None
    except Exception:
        log.exception("inbox sync skipped: cache layer unavailable")
        return None
    try:
        last = await cache.get(SYNC_LAST_KEY)
        since = (datetime.fromtimestamp(float(last), tz=timezone.utc) - SYNC_OVERLAP) if last else None
        if since is not None and not await _has_any_email(db):
            since = None
        started_at = datetime.now(timezone.utc).timestamp()
        before = None
        backfill = since is None
        if backfill:
            marker = await cache.get(SYNC_BACKFILL_KEY)
            if isinstance(marker, dict) and marker.get("before"):
                # Resume a crawl that was interrupted: keep its ORIGINAL start
                # time (everything newer than that is the incremental's job).
                before = datetime.fromisoformat(marker["before"])
                started_at = float(marker.get("started_at") or started_at)
        provider = get_email_provider()
        synced: list[EmailMessage] = []
        stored = 0
        async for page in _pages(provider, since, before):
            rows = [await sync_message(db, m) for m in page]
            await db.commit()
            stored += len(rows)
            if backfill:
                synced.extend(_light(r) for r in rows)
                db.expunge_all()          # don't hold a whole mailbox in the session
                stamps = [m.received_at for m in page if getattr(m, "received_at", None)]
                if stamps:
                    await cache.set(SYNC_BACKFILL_KEY,
                                    {"before": min(stamps).isoformat(), "started_at": started_at},
                                    ttl=14 * 24 * 3600)
            else:
                synced.extend(rows)
            await cache.extend_lock(SYNC_LOCK_KEY, token, _LOCK_TTL)
        await cache.set(SYNC_LAST_KEY, started_at)
        await cache.delete(SYNC_BACKFILL_KEY, SYNC_ERROR_KEY)
        await cache.set(SYNC_FRESH_KEY, True, ttl=settings.inbox_sync_min_interval_seconds)
        if backfill:
            log.info("inbox backfill complete: %d message(s) stored", stored)
        return synced
    except Exception as exc:
        log.exception("inbox sync failed (progress so far is kept; the cursor did not advance)")
        try:
            await db.rollback()
            await cache.set(SYNC_ERROR_KEY, f"{type(exc).__name__}: {str(exc)[:300]}", ttl=24 * 3600)
        except Exception:
            pass
        return None
    finally:
        try:
            await cache.release_lock(SYNC_LOCK_KEY, token)
        except Exception:
            pass
