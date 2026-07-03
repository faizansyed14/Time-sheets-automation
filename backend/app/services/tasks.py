"""
Celery tasks — background work.

  send_otp_email_task : deliver an OTP without blocking the login request.
  process_upload_task : (optional) run the extraction pipeline off-request.

With celery_task_always_eager=true (dev/tests) these run inline, so the same
code path is exercised whether or not a worker is running.
"""
from __future__ import annotations

import asyncio

from app.core.celery_app import celery_app


@celery_app.task(name="auth.send_otp_email", bind=True, max_retries=2, default_retry_delay=10)
def send_otp_email_task(self, email: str, code: str):
    from app.services.auth.email_otp import send_otp_email
    try:
        return send_otp_email(email, code)
    except Exception as exc:  # pragma: no cover - network dependent
        raise self.retry(exc=exc)


def _reset_async_clients() -> None:
    """Dispose async clients bound to the wrong/closed event loop.

    Celery prefork workers inherit module-level objects from the parent process.
    asyncpg/sqlalchemy connections and redis asyncio clients are tied to a loop;
    reusing them across task loops causes:
      - RuntimeError: Event loop is closed
      - RuntimeError: Future attached to a different loop
    """

    async def _cleanup() -> None:
        from app.core.cache import cache
        from app.core.database import engine

        # Reset redis client (if it was created) so next use re-inits cleanly.
        if getattr(cache, "_redis", None) is not None:
            try:
                await cache._redis.aclose()
            except Exception:
                pass
            cache._redis = None
            cache._checked = False

        # Drop all pooled DB connections (if any) so none cross loop boundaries.
        await engine.dispose()

    try:
        asyncio.run(_cleanup())
    except Exception:
        # Best-effort. Even if cleanup fails, ensure we don't keep a stale redis handle.
        try:
            from app.core.cache import cache

            cache._redis = None
            cache._checked = False
        except Exception:
            pass


def _run_coro(coro_factory):
    """Run async work to completion in a fresh event loop."""
    try:
        return asyncio.run(coro_factory())
    finally:
        _reset_async_clients()


@celery_app.task(name="maintenance.purge_pipeline_raw")
def purge_pipeline_raw_task():
    """Scheduled cleanup: delete pipeline retry copies older than the retention
    window (settings.pipeline_raw_retention_days). Runs daily via Celery beat;
    also invoked once on app startup as a safety net."""
    from app.services.pipeline import raw_store
    removed = raw_store.purge_old()
    return {"removed": removed}


@celery_app.task(name="ingestion.process_upload")
def process_upload_task(filename: str, content_type: str, data_b64: str):
    """Run the upload pipeline in a worker. Bytes are passed base64-encoded."""
    import base64

    from app.core.database import SessionLocal
    from app.services.pipeline.ingestion import ingest_upload

    data = base64.b64decode(data_b64)

    async def _run():
        async with SessionLocal() as db:
            rec, tracker = await ingest_upload(
                db, filename=filename, content_type=content_type, data=data)
            return {"pipeline_id": tracker.id, "record_id": rec.id if rec else None,
                    "status": tracker.status}

    return _run_coro(_run)
