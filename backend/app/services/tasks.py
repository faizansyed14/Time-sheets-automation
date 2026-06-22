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
