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


@celery_app.task(name="inbox.auto_extract_scan")
def auto_extract_scan_task(run_id: str):
    """The full-mailbox catch-up scan behind POST /inbox/auto-extract/start:
    lists every thread, works out which ones actually need (re-)extraction,
    then dispatches one extract_one_thread_task PER thread rather than
    looping through them here — this task's only job is the scan + dispatch,
    not the extraction work itself, so it returns quickly and the actual
    thread-by-thread work runs in parallel across the worker pool."""
    from app.services.extract_email import auto_extract
    return _run_coro(lambda: auto_extract._scan_and_dispatch(run_id))


@celery_app.task(name="inbox.extract_one_thread", bind=True, max_retries=2, default_retry_delay=15)
def extract_one_thread_task(self, pmid: str, subject: str, run_id: str):
    """ONE thread's extraction — the actual unit of Auto Extract work.
    Independent of every other thread's task: task_acks_late (see
    celery_app.py) means a worker dying mid-extraction gets this task
    redelivered to another worker instead of silently losing it, and one
    slow/stuck thread no longer blocks anything queued behind it (there IS
    nothing "behind it" in the old sense — every thread is its own task,
    picked up by whichever worker is free)."""
    from app.services.extract_email import auto_extract
    try:
        return _run_coro(lambda: auto_extract.run_one_thread(pmid, subject, run_id))
    except Exception as exc:  # pragma: no cover - network/model dependent
        raise self.retry(exc=exc)


@celery_app.task(name="inbox.sync")
def sync_inbox_task():
    """Pull new mail from the provider on a fixed schedule (Celery beat),
    independent of anyone having the Inbox page open — so new mail is
    already in the DB by the time someone looks, instead of only syncing
    as a side effect of loading the page. Shares the same throttle/lock as
    the on-demand sync in GET /inbox and /inbox/threads, so this never
    duplicates a sync a request just did (or vice versa).

    Also the ONLY place Auto Extract's "watch for new mail" mode reacts to
    new mail — deliberately not from the on-demand sync inside GET /inbox:
    that path runs inline in the request, and with CELERY_TASK_ALWAYS_EAGER
    (dev/tests) a triggered extraction would execute synchronously and block
    that page load. This task is already background work either way.

    Hands the exact rows sync_inbox just fetched to enqueue_new_threads,
    which enqueues extraction for JUST those threads — no full-mailbox
    rescan on every tick, unlike the old design. Most ticks find nothing new
    and skip straight past this."""
    from app.core.database import SessionLocal
    from app.services.extract_email import auto_extract
    from app.services.inbox.sync import sync_inbox

    async def _run():
        async with SessionLocal() as db:
            synced = await sync_inbox(db)
            if synced and await auto_extract.is_enabled():
                await auto_extract.enqueue_new_threads(db, synced)

    return _run_coro(_run)


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
