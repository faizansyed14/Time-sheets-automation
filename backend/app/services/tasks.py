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

    def _sync_reset() -> None:
        try:
            asyncio.run(_cleanup())
        except Exception:
            try:
                from app.core.cache import cache

                cache._redis = None
                cache._checked = False
            except Exception:
                pass

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        _sync_reset()
        return

    # pytest-asyncio (and other callers) may invoke eager Celery tasks while a
    # loop is already running — run cleanup on a dedicated thread/loop instead.
    import queue
    import threading

    q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            _sync_reset()
            q.put(None)
        except Exception as exc:
            q.put(exc)

    threading.Thread(target=_worker, daemon=True).start()
    err = q.get()
    if err is not None:
        raise err


def _run_coro(coro_factory):
    """Run async work to completion — safe from sync Celery and pytest-asyncio."""
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        try:
            return asyncio.run(coro_factory())
        finally:
            _reset_async_clients()

    import queue
    import threading

    q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            q.put(asyncio.run(coro_factory()))
        except Exception as exc:
            q.put(exc)
        finally:
            _reset_async_clients()

    threading.Thread(target=_worker, daemon=True).start()
    result = q.get()
    if isinstance(result, BaseException):
        raise result
    return result


@celery_app.task(name="maintenance.purge_pipeline_raw")
def purge_pipeline_raw_task():
    """Scheduled cleanup: delete pipeline retry copies older than the retention
    window (settings.pipeline_raw_retention_days). Runs daily via Celery beat;
    also invoked once on app startup as a safety net."""
    from app.services.pipeline import raw_store
    removed = raw_store.purge_old()
    return {"removed": removed}


@celery_app.task(name="inbox.auto_extract_all")
def auto_extract_all_task():
    """Bulk Extract Email: every thread in the inbox, one at a time, in the
    background. Started from POST /inbox/auto-extract/start; progress and
    stop live in app.services.extract_email.auto_extract (Redis-backed)."""
    from app.services.extract_email import auto_extract
    return _run_coro(auto_extract.run_auto_extract)


@celery_app.task(name="ingestion.process_upload")
def process_upload_task(filename: str, content_type: str, data_b64: str):
    """Stage an upload in a worker — the same Extract Email pipeline as the
    Upload page (analyse every sheet, group, stage for review)."""
    import base64

    from app.core.database import SessionLocal
    from app.services.agents.full_email_extract import extract_upload

    data = base64.b64decode(data_b64)

    async def _run():
        async with SessionLocal() as db:
            res = await extract_upload(
                db, filename=filename, content_type=content_type, data=data)
            staged = res["staged"]
            return {"staged": [t.id for t in staged],
                    "groups": res["groups"], "message": res["message"]}

    return _run_coro(_run)
