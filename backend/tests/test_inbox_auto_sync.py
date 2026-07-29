"""Background inbox sync — Celery beat calls this on a fixed schedule so new
mail is already in the DB before anyone opens the Inbox page (see
app.core.celery_app's "sync-inbox" beat entry)."""
import app.services.inbox.sync as sync_module
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
