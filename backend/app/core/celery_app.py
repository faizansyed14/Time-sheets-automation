"""
Celery application — background work queue (Redis broker/result backend).

Used for work that shouldn't block an HTTP request: sending OTP emails and
(optionally) running the extraction pipeline asynchronously.

In dev/tests `celery_task_always_eager=true` makes tasks run inline in the
calling process, so a worker/broker is NOT required to exercise the code. In
production a real worker is started by `scripts/prod/start.sh`.
"""
from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from app.core.config import settings

celery_app = Celery(
    "timesheet",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.services.tasks"],
)

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_max_tasks_per_child=200,
    broker_connection_retry_on_startup=True,
    # Periodic maintenance (runs under `celery worker -B` or a dedicated beat).
    # Interval configurable from .env (see config.Settings):
    #   PIPELINE_RAW_PURGE_INTERVAL_HOURS  — purge S3/disk retry copies (default daily)
    #   INBOX_AUTO_SYNC_INTERVAL_SECONDS   — background Outlook/Graph pull (default 60s)
    #   REMINDER_SCHEDULED_CHECK_ENABLED   — env kill-switch, default on (see below)
    #
    # reminders.scheduled_check has TWO independent gates, not one:
    #   1. REMINDER_SCHEDULED_CHECK_ENABLED (env, this file) — whether the beat
    #      entry exists at all. false means the task is never even scheduled,
    #      hourly or otherwise — the hard kill-switch for an environment like
    #      local dev where the DB may hold real employee data but nothing
    #      should ever actually send. Same on/off-at-process-startup technique
    #      as inbox_auto_sync_enabled just above.
    #   2. ReminderConfig.auto_send_enabled (DB, set from the Reminders page) —
    #      a runtime value gate 1 above can't express (a static beat_schedule
    #      entry is fixed at process-startup). When gate 1 is on, the task
    #      still fires hourly but only actually sends when this DB switch is
    #      also on AND it's the 28th/9am UAE AND today's run hasn't already
    #      happened — see services/reminders/service.py's run_scheduled_check.
    beat_schedule={
        "purge-pipeline-raw": {
            "task": "maintenance.purge_pipeline_raw",
            "schedule": max(60.0, settings.pipeline_raw_purge_interval_hours * 3600.0),
        },
        **({
            "reminders-scheduled-check": {
                "task": "reminders.scheduled_check",
                "schedule": 3600.0,
            },
        } if settings.reminder_scheduled_check_enabled else {}),
        **({
            "sync-inbox": {
                "task": "inbox.sync",
                "schedule": max(30.0, float(settings.inbox_auto_sync_interval_seconds)),
            },
        } if settings.inbox_auto_sync_enabled else {}),
        # SYSTEM_HEALTH_CHECK_ENABLED — same on/off-at-startup technique as
        # the two gates above, but for an entirely separate feature (see
        # services/system_health/monitor.py's module docstring for why it's
        # kept isolated from reminders/OTP rather than reusing either).
        **({
            "system-health-check": {
                "task": "system_health.check",
                "schedule": max(300.0, settings.system_health_check_interval_hours * 3600.0),
            },
        } if settings.system_health_check_enabled else {}),
    },
)


@worker_process_init.connect
def _celery_worker_process_init(**_kwargs) -> None:
    """After prefork, discard async clients inherited from the parent process."""
    from app.services.tasks import _reset_async_clients

    _reset_async_clients()
