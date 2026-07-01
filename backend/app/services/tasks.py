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


def _run_coro(coro_factory):
    """Run an async coroutine to completion in its own event loop, in a dedicated
    thread. Works whether or not the caller already has a running loop (so the
    task behaves the same in a real worker and in eager mode inside async tests)."""
    import threading

    box: dict = {}

    def runner():
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro_factory())
        except Exception as e:  # surface inside the calling thread
            box["error"] = e
        finally:
            loop.close()

    t = threading.Thread(target=runner)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


@celery_app.task(name="maintenance.purge_pipeline_raw")
def purge_pipeline_raw_task():
    """Scheduled cleanup: delete pipeline retry copies older than the retention
    window (settings.pipeline_raw_retention_days). Runs daily via Celery beat;
    also invoked once on app startup as a safety net."""
    from app.services.pipeline import raw_store
    removed = raw_store.purge_old()
    return {"removed": removed}


@celery_app.task(name="inbox.ai_check_scan")
def ai_check_inbox_task(limit: int = 100):
    """Background pass: AI-check every inbox email not yet checked.

    Runs off-request (inbox sync, app open, Celery beat). Already-checked
    emails (ai_check is not None) are never reprocessed."""
    from sqlalchemy import select

    from app.core.cache import cache
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.email_message import EmailMessage
    from app.services.email_provider import get_email_provider
    from app.services.inbox.ai_check import ensure_ai_check

    async def _run():
        limit_eff = max(1, int(limit or settings.inbox_ai_check_batch))
        async with SessionLocal() as db:
            try:
                lock_key = "inbox:sync:lock"
                if not await cache.exists(lock_key):
                    await cache.set(lock_key, True, ttl=30)
                    try:
                        provider = get_email_provider()
                        from app.api.routes.inbox import _sync_message
                        for m in await provider.list_messages(None):
                            await _sync_message(db, m)
                        await db.commit()
                    finally:
                        await cache.delete(lock_key)
            except Exception:
                pass

            rows = (await db.execute(
                select(EmailMessage)
                .where(EmailMessage.ai_check.is_(None))
                .order_by(EmailMessage.received_at.desc())
                .limit(limit_eff)
            )).scalars().all()

            checked = 0
            for row in rows:
                try:
                    await ensure_ai_check(db, row, force=False)
                    await db.commit()
                    checked += 1
                except Exception:
                    await db.rollback()
                    continue
            return {"checked": checked, "scanned": len(rows)}

    return _run_coro(_run)


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
