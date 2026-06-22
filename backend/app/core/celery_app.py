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
)
