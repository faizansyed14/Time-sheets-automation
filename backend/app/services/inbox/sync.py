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


async def sync_inbox(db: AsyncSession) -> None:
    """Throttled, incremental provider sync. Never blocks the caller on a full
    mailbox download:

    - fresh (synced < INBOX_SYNC_MIN_INTERVAL_SECONDS ago) → no-op, serve DB;
    - otherwise ask the provider only for messages received after the LAST
      SUCCESSFUL SYNC (one small request). A full folder crawl happens only
      when there is no sync marker (first boot / cache flushed);
    - any provider/cache error → serve existing DB rows, never raise.
    """
    try:
        if await cache.exists(SYNC_FRESH_KEY) or await cache.exists(SYNC_LOCK_KEY):
            return
        await cache.set(SYNC_LOCK_KEY, True, ttl=60)
    except Exception:
        return  # cache layer down → skip sync, DB rows still serve
    try:
        last = await cache.get(SYNC_LAST_KEY)
        since = (datetime.fromtimestamp(float(last), tz=timezone.utc) - SYNC_OVERLAP) if last else None
        started_at = datetime.now(timezone.utc).timestamp()
        provider = get_email_provider()
        for m in await provider.list_messages(None, since=since):
            await sync_message(db, m)
        await db.commit()
        await cache.set(SYNC_LAST_KEY, started_at)
        await cache.set(SYNC_FRESH_KEY, True, ttl=settings.inbox_sync_min_interval_seconds)
    except Exception:
        await db.rollback()
    finally:
        try:
            await cache.delete(SYNC_LOCK_KEY)
        except Exception:
            pass
