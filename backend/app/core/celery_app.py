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
    beat_schedule={
        "purge-pipeline-raw": {
            "task": "maintenance.purge_pipeline_raw",
            "schedule": max(60.0, settings.pipeline_raw_purge_interval_hours * 3600.0),
        },
    },
)


@worker_process_init.connect
def _celery_worker_process_init(**_kwargs) -> None:
    """After prefork, discard async clients inherited from the parent process."""
    from app.services.tasks import _reset_async_clients

    _reset_async_clients()
