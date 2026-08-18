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


async def test_sync_inbox_returns_message_count_or_none_when_skipped(monkeypatch):
    """sync_inbox_task uses this return value to decide whether it's even
    worth checking Auto Extract's watch mode -- must be an int (message
    count, possibly 0) whenever a sync actually ran, and None when this
    particular call was a no-op (still fresh, or another sync holds the
    lock) so a quiet tick never gets mistaken for "found new mail"."""
    await cache.delete(sync_module.SYNC_FRESH_KEY, sync_module.SYNC_LOCK_KEY, sync_module.SYNC_LAST_KEY)

    class _TwoMessageProvider:
        async def list_messages(self, query=None, since=None):
            return [object(), object()]

    async def _fake_sync_message(db, m):
        return None

    monkeypatch.setattr(sync_module, "sync_message", _fake_sync_message)
    monkeypatch.setattr(sync_module, "get_email_provider", lambda: _TwoMessageProvider())

    try:
        async with SessionLocal() as db:
            result = await sync_module.sync_inbox(db)
        assert result == 2

        # Right after a successful sync, the fresh-window means an
        # immediate second call is skipped outright.
        async with SessionLocal() as db:
            skipped = await sync_module.sync_inbox(db)
        assert skipped is None
    finally:
        await cache.delete(sync_module.SYNC_LAST_KEY, sync_module.SYNC_FRESH_KEY, sync_module.SYNC_LOCK_KEY)


async def test_sync_inbox_task_triggers_auto_extract_when_enabled_and_new_mail_arrived(monkeypatch):
    """The background sync tick is deliberately the ONLY place Auto
    Extract's watch mode re-triggers a run (see sync_inbox_task's
    docstring) -- must fire exactly when this tick both found new mail AND
    the mode is on, and must stay quiet on an empty tick, a skipped tick,
    or while the mode is off."""
    from app.services.extract_email import auto_extract

    calls = {"n": 0}

    async def _fake_start():
        calls["n"] += 1
        return {}

    async def _found_mail(db):
        return 3

    async def _quiet(db):
        return 0

    async def _tick_skipped(db):
        return None

    monkeypatch.setattr(auto_extract, "start", _fake_start)
    await cache.delete(auto_extract._ENABLED_KEY)

    try:
        # Mode off, mail arrived -> must not trigger.
        monkeypatch.setattr(sync_module, "sync_inbox", _found_mail)
        tasks_module.sync_inbox_task()
        assert calls["n"] == 0

        # Mode on, mail arrived -> triggers.
        await cache.set(auto_extract._ENABLED_KEY, True)
        tasks_module.sync_inbox_task()
        assert calls["n"] == 1

        # Mode on, quiet tick (0 messages) -> does not trigger again.
        monkeypatch.setattr(sync_module, "sync_inbox", _quiet)
        tasks_module.sync_inbox_task()
        assert calls["n"] == 1

        # Mode on, tick skipped entirely (None) -> does not trigger.
        monkeypatch.setattr(sync_module, "sync_inbox", _tick_skipped)
        tasks_module.sync_inbox_task()
        assert calls["n"] == 1
    finally:
        await cache.delete(auto_extract._ENABLED_KEY)
