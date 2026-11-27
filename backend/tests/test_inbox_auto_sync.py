"""Background inbox sync — Celery beat calls this on a fixed schedule so new
mail is already in the DB before anyone opens the Inbox page (see
app.core.celery_app's "sync-inbox" beat entry)."""
import time

import app.services.inbox.sync as sync_module
from app.core.cache import cache
from app.core.database import SessionLocal
from app.services import tasks as tasks_module


def test_sync_inbox_task_is_registered_and_runs_the_shared_sync(monkeypatch):
    calls = []

    async def _fake_sync_inbox(db):
        calls.append(db)

    monkeypatch.setattr(sync_module, "sync_inbox", _fake_sync_inbox)

    tasks_module.sync_inbox_task()  # CELERY_TASK_ALWAYS_EAGER=true in tests -> runs inline

    assert len(calls) == 1


def test_beat_schedule_includes_inbox_sync_when_enabled():
    from app.core.celery_app import celery_app
    from app.core.config import settings

    schedule = celery_app.conf.beat_schedule
    assert settings.inbox_auto_sync_enabled  # test env default
    assert "sync-inbox" in schedule
    assert schedule["sync-inbox"]["task"] == "inbox.sync"


class _FakeProvider:
    def __init__(self):
        self.seen_since = []

    async def list_messages(self, query=None, since=None):
        self.seen_since.append(since)
        return []


async def test_stale_redis_cursor_forces_full_crawl_when_db_is_empty(monkeypatch):
    """Regression: Postgres wiped (or restored) without also clearing the
    Redis sync cursor — the cursor says "already caught up as of an hour
    ago", but the table has nothing. Must ignore the cursor and do a full
    crawl instead of asking the provider for "just what's new" and staying
    empty forever."""
    await cache.set(sync_module.SYNC_LAST_KEY, time.time() - 3600)
    await cache.delete(sync_module.SYNC_FRESH_KEY, sync_module.SYNC_LOCK_KEY)

    async def _fake_has_any(db):
        return False

    async def _fake_sync_message(db, m):
        return None

    provider = _FakeProvider()
    monkeypatch.setattr(sync_module, "_has_any_email", _fake_has_any)
    monkeypatch.setattr(sync_module, "sync_message", _fake_sync_message)
    monkeypatch.setattr(sync_module, "get_email_provider", lambda: provider)

    try:
        async with SessionLocal() as db:
            await sync_module.sync_inbox(db)
        assert provider.seen_since == [None]
    finally:
        await cache.delete(sync_module.SYNC_LAST_KEY, sync_module.SYNC_FRESH_KEY, sync_module.SYNC_LOCK_KEY)


async def test_valid_cursor_is_respected_when_the_db_has_rows(monkeypatch):
    """The normal case: a real cursor with real data behind it still does
    the cheap incremental pull, not a full crawl every time."""
    cursor_time = time.time() - 3600
    await cache.set(sync_module.SYNC_LAST_KEY, cursor_time)
    await cache.delete(sync_module.SYNC_FRESH_KEY, sync_module.SYNC_LOCK_KEY)

    async def _fake_has_any(db):
        return True

    async def _fake_sync_message(db, m):
        return None

    provider = _FakeProvider()
    monkeypatch.setattr(sync_module, "_has_any_email", _fake_has_any)
    monkeypatch.setattr(sync_module, "sync_message", _fake_sync_message)
    monkeypatch.setattr(sync_module, "get_email_provider", lambda: provider)

    try:
        async with SessionLocal() as db:
            await sync_module.sync_inbox(db)
        assert len(provider.seen_since) == 1
        assert provider.seen_since[0] is not None
    finally:
        await cache.delete(sync_module.SYNC_LAST_KEY, sync_module.SYNC_FRESH_KEY, sync_module.SYNC_LOCK_KEY)


async def test_sync_inbox_returns_synced_rows_or_none_when_skipped(monkeypatch):
    """sync_inbox_task hands this return value straight to
    auto_extract.enqueue_new_threads (which threads need extraction), so it
    must be the actual synced rows (possibly an empty list) whenever a sync
    ran, and None when this particular call was a no-op (still fresh, or
    another sync holds the lock) so a quiet tick never gets mistaken for
    "found new mail"."""
    await cache.delete(sync_module.SYNC_FRESH_KEY, sync_module.SYNC_LOCK_KEY, sync_module.SYNC_LAST_KEY)

    class _TwoMessageProvider:
        async def list_messages(self, query=None, since=None):
            return [object(), object()]

    async def _fake_sync_message(db, m):
        return object()  # stand-in EmailMessage row

    monkeypatch.setattr(sync_module, "sync_message", _fake_sync_message)
    monkeypatch.setattr(sync_module, "get_email_provider", lambda: _TwoMessageProvider())

    try:
        async with SessionLocal() as db:
            result = await sync_module.sync_inbox(db)
        assert len(result) == 2

        # Right after a successful sync, the fresh-window means an
        # immediate second call is skipped outright.
        async with SessionLocal() as db:
            skipped = await sync_module.sync_inbox(db)
        assert skipped is None
    finally:
        await cache.delete(sync_module.SYNC_LAST_KEY, sync_module.SYNC_FRESH_KEY, sync_module.SYNC_LOCK_KEY)


async def test_sync_inbox_task_enqueues_new_mail_only_when_enabled(monkeypatch):
    """The background tick queues every message INSERTED since its last tick (whichever
    sync stored it), only while Auto Extract is on, and each message only once."""
    from app.models.email_message import EmailMessage
    from app.services.extract_email import auto_extract
    from sqlalchemy import delete

    calls: list[list] = []

    async def _fake_enqueue(db, rows):
        calls.append([r.provider_message_id for r in rows])
        return {}

    async def _quiet(db):
        return []

    monkeypatch.setattr(auto_extract, "enqueue_new_threads", _fake_enqueue)
    monkeypatch.setattr(sync_module, "sync_inbox", _quiet)
    await cache.delete(auto_extract._ENABLED_KEY, auto_extract._WATERMARK_KEY)
    pmids = ["SYNC-T-1", "SYNC-T-2"]

    async def _store(pmid):
        async with SessionLocal() as db:
            db.add(EmailMessage(provider_message_id=pmid, sender_name="S", sender_email="s@x.y",
                                subject=pmid, body_text="", attachments=[],
                                to_recipients=[], cc_recipients=[]))
            await db.commit()

    try:
        # Mode off, mail arrived -> nothing queued.
        await _store(pmids[0])
        tasks_module.sync_inbox_task()
        assert calls == []

        # Mode on, new mail -> queued.
        await cache.set(auto_extract._ENABLED_KEY, True)
        await _store(pmids[1])
        tasks_module.sync_inbox_task()
        assert any(pmids[1] in c for c in calls)
        queued = sum(c.count(pmids[1]) for c in calls)

        # Quiet tick -> the same message is not queued again.
        tasks_module.sync_inbox_task()
        assert sum(c.count(pmids[1]) for c in calls) == queued == 1
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.in_(pmids)))
            await db.commit()
        await cache.delete(auto_extract._ENABLED_KEY, auto_extract._WATERMARK_KEY,
                           *[f"auto_extract:seen:{p}" for p in pmids])


async def test_a_page_request_never_downloads_the_whole_mailbox(monkeypatch):
    """With no sync position (first run / Redis cleared) the full download is left to the
    background worker; the Inbox request returns at once instead of waiting minutes."""
    from app.core.cache import cache as _cache

    called = []

    class _Provider:
        async def list_messages(self, query=None, since=None):
            called.append(since)
            return []

    monkeypatch.setattr(sync_module, "get_email_provider", lambda: _Provider())
    await _cache.delete(sync_module.SYNC_LAST_KEY, sync_module.SYNC_FRESH_KEY,
                        sync_module.SYNC_BACKFILL_KEY, sync_module.SYNC_LOCK_KEY)
    async with SessionLocal() as db:
        assert await sync_module.sync_inbox(db, allow_backfill=False) is None
    assert called == [], "no mailbox download inside a page request"
