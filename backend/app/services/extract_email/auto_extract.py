"""Background bulk Extract Email — every thread in the inbox gets its own
independent Celery task instead of one big loop, so the work actually scales:

  - NEW MAIL doesn't trigger a full-mailbox rescan. sync_inbox already knows
    exactly which messages just arrived; sync_inbox_task hands those straight
    to `enqueue_new_threads`, which enqueues extraction for just those threads
    (or, if nothing's running, starts a fresh tiny run scoped to only them).
    The expensive "walk every thread in the mailbox" scan only happens when
    a human explicitly presses Start (`start()`) — an occasional, deliberate
    catch-up sweep, not a cost paid on every ~60s tick forever.
  - Each thread's extraction is its OWN Celery task (`inbox.extract_one_thread`
    in app.services.tasks), picked up by whichever worker is free. A slow or
    stuck thread no longer blocks everything queued behind it, and Celery's
    own `task_acks_late` redelivers a task whose worker died mid-extraction
    instead of silently losing it.
  - Progress is tracked with ATOMIC per-run counters (cache.incr), not a
    single JSON blob rewritten by one sequential loop — safe for many
    concurrent tasks completing at once. A `run_id` scopes every counter, so
    a straggling task from a run that's since finished or was stopped can
    never corrupt a newer run's numbers.
  - A thread whose last real check found NOTHING (EmailMessage.
    no_sheets_found_at) now counts as "already checked" the same way a
    successful extraction does — previously only PipelineFile-backed
    extractions were remembered, so a plain "no timesheet here" email was
    silently re-sent through the vision model on every single re-trigger,
    forever.

Status is kept in the shared cache (Redis in Docker; in-memory fallback if
Redis is unreachable), polled by the UI. The external shape of
GET /auto-extract/status (state/total/processed/succeeded/failed/skipped/
current/started_at/finished_at/last_error/enabled) is UNCHANGED — only how
it's produced underneath is different.

Stop is still cooperative, now at per-task granularity: request_stop() marks
the run's stop flag; any task not yet past its own stop-check (whether still
queued or about to start) exits immediately without doing its extraction. A
task already mid-extraction finishes naturally — never a mid-write kill.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.models.email_message import EmailMessage, EmailStatus
from app.models.pipeline_file import PipelineFile
from app.services.extract_email.constants import TAG_PREFIX

# Long-lived on/off switch — separate from any one run's status. This is
# "should a background sync tick start/extend a run on its own", and
# outlives any single run's completion.
_ENABLED_KEY = "auto_extract:enabled"
_ENABLED_TTL = 30 * 24 * 3600  # 30 days — a deliberate mode, not run bookkeeping
# Refreshed on every update while running; a safety net so a run's keys never
# linger forever if every task handling it dies without cleaning up.
_RUN_TTL = 6 * 3600

_RUN_ID_KEY = "auto_extract:run_id"

_IDLE_STATUS: dict = {
    "state": "idle",
    "total": 0, "processed": 0, "succeeded": 0, "failed": 0, "skipped": 0,
    "current": None, "started_at": None, "finished_at": None, "last_error": None,
}

Anchor = tuple[str, str, str, "dt.datetime | None"]  # pmid, subject, thread_key, newest_at


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _rk(run_id: str, field: str) -> str:
    return f"auto_extract:run:{run_id}:{field}"


async def is_enabled() -> bool:
    """Whether Auto Extract's watch-for-new-mail mode is on — checked by
    sync_inbox_task before it enqueues/extends a run off the back of a
    background sync. Independent of whether a run happens to be in flight."""
    return bool(await cache.get(_ENABLED_KEY))


async def _set_enabled(value: bool) -> None:
    if value:
        await cache.set(_ENABLED_KEY, True, ttl=_ENABLED_TTL)
    else:
        await cache.delete(_ENABLED_KEY)


async def _current_run_id() -> str | None:
    return await cache.get(_RUN_ID_KEY)


async def get_status() -> dict:
    """Compose the external status shape from a run's atomic counters.
    `processed` is derived (skipped + succeeded + failed), never itself
    incremented, so it can't drift out of sync with its parts."""
    run_id = await _current_run_id()
    if not run_id:
        return {**_IDLE_STATUS, "enabled": await is_enabled()}
    state = await cache.get(_rk(run_id, "state"))
    if not state:
        return {**_IDLE_STATUS, "enabled": await is_enabled()}
    total = int(await cache.get(_rk(run_id, "total")) or 0)
    skipped = int(await cache.get(_rk(run_id, "skipped")) or 0)
    succeeded = int(await cache.get(_rk(run_id, "succeeded")) or 0)
    failed = int(await cache.get(_rk(run_id, "failed")) or 0)
    stopped_early = int(await cache.get(_rk(run_id, "stopped_early")) or 0)
    processed = skipped + succeeded + failed + stopped_early
    return {
        "state": state,
        "total": total, "processed": processed, "succeeded": succeeded,
        "failed": failed, "skipped": skipped,
        "current": await cache.get(_rk(run_id, "current")),
        "started_at": await cache.get(_rk(run_id, "started_at")),
        "finished_at": await cache.get(_rk(run_id, "finished_at")),
        "last_error": await cache.get(_rk(run_id, "last_error")),
        "enabled": await is_enabled(),
    }


async def request_stop() -> dict:
    """Turn Auto Extract off: mark the active run's stop flag (every queued
    or about-to-start task checks it and exits without doing work) and clear
    the persistent `enabled` flag so a later sync tick doesn't start a new
    run right back up."""
    await _set_enabled(False)
    run_id = await _current_run_id()
    if run_id:
        state = await cache.get(_rk(run_id, "state"))
        if state == "running":
            await cache.set(_rk(run_id, "stop"), True, ttl=_RUN_TTL)
            await cache.set(_rk(run_id, "state"), "stopping", ttl=_RUN_TTL)
    return await get_status()


async def _stop_requested(run_id: str) -> bool:
    return bool(await cache.get(_rk(run_id, "stop")))


async def _list_all_thread_anchors(db: AsyncSession) -> list[Anchor]:
    """(provider_message_id, subject, thread_key, newest received_at) for
    every thread, newest-first — one row per Outlook-style conversation, the
    same grouping GET /inbox/threads uses, but every thread and no page
    limit. Archived threads are excluded (archiving is an explicit "not this
    one"). Only used for the full catch-up scan (start()) — the steady-state
    new-mail path never calls this."""
    group_key = func.coalesce(EmailMessage.conversation_id, EmailMessage.id)
    # Columns only - never whole EmailMessage rows: a mailbox with hundreds of
    # thousands of threads must not pull every message body into memory just
    # to learn four fields per thread.
    stmt = (
        select(EmailMessage.provider_message_id, EmailMessage.subject,
               EmailMessage.conversation_id, EmailMessage.received_at)
        .where(EmailMessage.status != EmailStatus.ARCHIVED)
        .distinct(group_key)
        .order_by(group_key, EmailMessage.received_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    rows = sorted(rows, key=lambda r: r.received_at or epoch, reverse=True)
    return [
        (r.provider_message_id, r.subject or "(no subject)",
         r.conversation_id or r.provider_message_id, r.received_at)
        for r in rows
    ]


async def _anchors_for_pmids(db: AsyncSession, pmids: list[str]) -> list[Anchor]:
    """Same anchor shape as _list_all_thread_anchors, but scoped to specific
    messages (the ones sync_inbox just fetched) - for the incremental path,
    which must never touch the rest of the mailbox. Chunked and column-only so
    a large first sync (tens of thousands of new messages) neither exceeds the
    driver's bind-parameter limit nor loads every message body."""
    if not pmids:
        return []
    thread_keys: set[str] = set()
    for i in range(0, len(pmids), 5000):
        for pmid, conv in (await db.execute(
            select(EmailMessage.provider_message_id, EmailMessage.conversation_id).where(
                EmailMessage.provider_message_id.in_(pmids[i:i + 5000]),
                EmailMessage.status != EmailStatus.ARCHIVED,
            )
        )).all():
            thread_keys.add(conv or pmid)
    if not thread_keys:
        return []
    # Resolve each to its THREAD's actual newest message (a "new" reply may
    # not itself be the newest anchor if two arrived out of order), deduped
    # by thread - mirrors _list_all_thread_anchors' grouping, scoped small.
    group_key = func.coalesce(EmailMessage.conversation_id, EmailMessage.id)
    keys = sorted(thread_keys)
    anchor_rows: list = []
    for i in range(0, len(keys), 5000):
        stmt = (
            select(EmailMessage.provider_message_id, EmailMessage.subject,
                   EmailMessage.conversation_id, EmailMessage.received_at)
            .where(
                EmailMessage.status != EmailStatus.ARCHIVED,
                func.coalesce(EmailMessage.conversation_id,
                              EmailMessage.provider_message_id).in_(keys[i:i + 5000]),
            )
            .distinct(group_key)
            .order_by(group_key, EmailMessage.received_at.desc())
        )
        anchor_rows += (await db.execute(stmt)).all()
    epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    anchor_rows.sort(key=lambda r: r.received_at or epoch, reverse=True)
    return [
        (r.provider_message_id, r.subject or "(no subject)",
         r.conversation_id or r.provider_message_id, r.received_at)
        for r in anchor_rows
    ]


async def _partition_needs_extraction(db: AsyncSession, anchors: list[Anchor]) -> tuple[list[Anchor], list[Anchor]]:
    """Split anchors into (skip_now, need_real) — skip_now = nothing new
    since we last looked, WHETHER that look found something to stage
    (PipelineFile, via sheet_cache.last_extraction_at) or genuinely found
    NOTHING (EmailMessage.no_sheets_found_at). Previously only the first case
    counted, so a legitimate "no timesheet in this thread" email had no
    durable memory at all and got re-sent through the vision model on every
    single re-trigger, forever — this is what fixes that."""
    from app.services.extract_email import sheet_cache

    if not anchors:
        return [], []
    thread_keys = [tk for _, _, tk, _ in anchors]
    last_extract_by_thread = await sheet_cache.last_extraction_at_bulk(db, thread_keys)

    pmids = [pmid for pmid, _, _, _ in anchors]
    no_sheets_rows: list = []
    for i in range(0, len(pmids), 5000):      # chunked - see sheet_cache.last_extraction_at_bulk
        no_sheets_rows += (await db.execute(
            select(EmailMessage.provider_message_id, EmailMessage.no_sheets_found_at)
            .where(EmailMessage.provider_message_id.in_(pmids[i:i + 5000]))
        )).all()
    # A "nothing here" mark from before the extraction rules last improved is not
    # trusted - those are exactly the threads whose timesheets the old limits
    # (size filter, page/message caps, failed calls) could have hidden.
    from app.core.config import settings
    try:
        rules_at = dt.datetime.fromisoformat(settings.extract_rules_revision_at)
    except (TypeError, ValueError):
        rules_at = None
    no_sheets_by_pmid = {
        pmid: at for pmid, at in no_sheets_rows
        if at is not None and (rules_at is None or at >= rules_at)}

    skip_now: list[Anchor] = []
    need_real: list[Anchor] = []
    for anchor in anchors:
        pmid, _, thread_key, newest_at = anchor
        checked_candidates = [t for t in (last_extract_by_thread.get(thread_key),
                                          no_sheets_by_pmid.get(pmid)) if t is not None]
        checked_at = max(checked_candidates) if checked_candidates else None
        if checked_at is not None and newest_at is not None and checked_at >= newest_at:
            skip_now.append(anchor)
        else:
            need_real.append(anchor)
    return skip_now, need_real


async def coverage(db: AsyncSession) -> dict:
    """How many threads, mailbox-wide, show the Extracted badge right now —
    computed live off the same PipelineFile tag the Inbox's own per-thread
    badge and the skip check above both read, not a separately-maintained
    counter that could drift from what's actually displayed. Backs the
    small "Details" panel next to the Auto Extract button."""
    thread_key = func.coalesce(EmailMessage.conversation_id, EmailMessage.provider_message_id)
    total = (await db.execute(
        select(func.count(func.distinct(thread_key)))
    )).scalar_one()
    extracted = (await db.execute(
        select(func.count(func.distinct(thread_key))).where(
            select(PipelineFile.id).where(
                PipelineFile.source_kind == "email",
                PipelineFile.thread_key == thread_key,
                PipelineFile.attachment_id.like(f"{TAG_PREFIX}%"),
            ).exists()
        )
    )).scalar_one()
    return {"extracted_threads": extracted, "total_threads": total}


async def _new_run(*, total: int, skipped: int) -> str:
    run_id = uuid.uuid4().hex[:12]
    started_at = _now_iso()
    for field, value in (
        ("state", "running"), ("total", total), ("skipped", skipped),
        ("succeeded", 0), ("failed", 0), ("stopped_early", 0),
        ("started_at", started_at), ("finished_at", None),
        ("last_error", None), ("current", None),
    ):
        await cache.set(_rk(run_id, field), value, ttl=_RUN_TTL)
    await cache.set(_RUN_ID_KEY, run_id, ttl=_RUN_TTL)
    return run_id


async def _extend_run(run_id: str, *, extra_total: int) -> None:
    """Add more work to an ALREADY RUNNING run — used when new mail arrives
    mid-run. Extending `total` (rather than starting a second run) keeps one
    coherent progress bar instead of two overlapping ones. cache.incr only
    steps by 1, but each step is atomic — looping it is still race-free
    against a concurrent extend, unlike a single read-modify-write."""
    for _ in range(max(0, extra_total)):
        await cache.incr(_rk(run_id, "total"), ttl=_RUN_TTL)


# A thread already queued/being worked for THIS run is not queued again (a sync
# tick and a catch-up scan can both want the same thread). The marker lives for
# the run's TTL; run_one_thread clears it when the thread finishes.
def _queued_key(run_id: str, pmid: str) -> str:
    return f"auto_extract:run:{run_id}:queued:{pmid}"


async def _warn_if_no_extract_worker(run_id: str) -> None:
    """Extraction tasks are routed to the `extract` queue (served by the
    worker-extract service). If nothing is consuming it - e.g. someone started
    only `backend worker` - the run would sit at 0% forever with no explanation.
    Say so in the run's last_error, which the UI shows. Best-effort, never raises."""
    import asyncio

    from app.core.celery_app import celery_app
    from app.core.config import settings

    if settings.celery_task_always_eager:
        return

    def _probe() -> bool:
        try:
            queues = celery_app.control.inspect(timeout=1.5).active_queues() or {}
        except Exception:
            return True          # can't tell - don't cry wolf
        if not queues:
            return False
        return any(q.get("name") == "extract" for qs in queues.values() for q in qs)

    try:
        ok = await asyncio.to_thread(_probe)
    except Exception:
        return
    if not ok:
        await cache.set(
            _rk(run_id, "last_error"),
            "No worker is consuming the 'extract' queue - start the worker-extract service "
            "(docker compose ... up -d worker-extract). Queued threads will wait until it runs.",
            ttl=_RUN_TTL)


def _dispatch(pmid: str, subject: str, run_id: str) -> None:
    from app.services.tasks import extract_one_thread_task
    extract_one_thread_task.delay(pmid, subject, run_id)


async def _dispatch_many(anchors: list[Anchor], run_id: str) -> int:
    """Queue one task per anchor, skipping any already queued for this run.
    Returns how many were skipped as duplicates so the caller can keep the
    run's total/processed arithmetic balanced."""
    dupes = 0
    for pmid, subject, _thread_key, _newest_at in anchors:
        if not await cache.acquire_lock(_queued_key(run_id, pmid), _RUN_TTL, "queued"):
            dupes += 1
            continue
        _dispatch(pmid, subject, run_id)
    return dupes


async def _mark_done_once(run_id: str, pmid: str) -> bool:
    """True the first time a thread is finished for this run. A redelivered
    task (worker died after finishing, before acking) must not bump the
    counters a second time and end the run early."""
    return await cache.acquire_lock(f"auto_extract:run:{run_id}:done:{pmid}", _RUN_TTL, "done")


async def start() -> dict:
    """Kick off a full catch-up sweep — fast, returns immediately without
    scanning the mailbox on this (request) path; the scan happens inside the
    dispatched task, same as the old design's delegate-to-Celery shape. Also
    turns on the persistent watch-for-new-mail mode so new mail keeps
    getting picked up afterwards without this needing to be pressed again.
    Idempotent — if a run is already active, returns its current status
    instead of starting a second overlapping one."""
    await _set_enabled(True)
    status = await get_status()
    if status.get("state") in ("running", "stopping"):
        return status

    run_id = await _new_run(total=0, skipped=0)
    from app.services.tasks import auto_extract_scan_task
    auto_extract_scan_task.delay(run_id)
    return await get_status()


async def _scan_and_dispatch(run_id: str) -> None:
    """The actual full-mailbox catch-up scan for start() — runs inside
    auto_extract_scan_task, never on the request path. Fills in this run's
    real total/skipped once counted, then dispatches one task per thread
    that needs (re-)extraction."""
    from app.core.database import SessionLocal
    async with SessionLocal() as db:
        anchors = await _list_all_thread_anchors(db)
        skip_now, need_real = await _partition_needs_extraction(db, anchors)

    await cache.set(_rk(run_id, "total"), len(anchors), ttl=_RUN_TTL)
    await cache.set(_rk(run_id, "skipped"), len(skip_now), ttl=_RUN_TTL)
    dupes = await _dispatch_many(need_real, run_id)
    if need_real:
        await _warn_if_no_extract_worker(run_id)
    if dupes:
        await cache.set(_rk(run_id, "skipped"), len(skip_now) + dupes, ttl=_RUN_TTL)
    if not need_real or dupes == len(need_real):
        await _finish_if_done(run_id)


async def enqueue_new_threads(db: AsyncSession, rows: list[EmailMessage]) -> dict | None:
    """Incremental path — called by sync_inbox_task with EXACTLY the messages
    that just arrived. Never touches the rest of the mailbox: resolves those
    messages to their thread anchors, skips any that turn out to already be
    current (a reply to an already-extracted thread with nothing new to add
    is possible), and either extends the active run or starts a small fresh
    one for just this batch. Returns None if Auto Extract isn't enabled or
    there's nothing that actually needs extraction."""
    if not rows or not await is_enabled():
        return None
    pmids = [r.provider_message_id for r in rows]
    anchors = await _anchors_for_pmids(db, pmids)
    _skip_now, need_real = await _partition_needs_extraction(db, anchors)
    if not need_real:
        return None

    status = await get_status()
    run_id = await _current_run_id()
    if run_id and status.get("state") in ("running", "stopping"):
        if status.get("state") == "stopping":
            return None  # winding down — don't feed it more work
        await _extend_run(run_id, extra_total=len(need_real))
    else:
        run_id = await _new_run(total=len(need_real), skipped=0)
    dupes = await _dispatch_many(need_real, run_id)
    await _warn_if_no_extract_worker(run_id)
    for _ in range(dupes):          # already queued: count as processed so the bar balances
        await cache.incr(_rk(run_id, "skipped"), ttl=_RUN_TTL)
    if dupes == len(need_real):
        await _finish_if_done(run_id)
    return await get_status()


async def _finish_if_done(run_id: str) -> None:
    total = int(await cache.get(_rk(run_id, "total")) or 0)
    skipped = int(await cache.get(_rk(run_id, "skipped")) or 0)
    succeeded = int(await cache.get(_rk(run_id, "succeeded")) or 0)
    failed = int(await cache.get(_rk(run_id, "failed")) or 0)
    stopped_early = int(await cache.get(_rk(run_id, "stopped_early")) or 0)
    if skipped + succeeded + failed + stopped_early < total:
        return
    state = await cache.get(_rk(run_id, "state"))
    if state in ("completed", "stopped"):
        return
    final = "stopped" if await _stop_requested(run_id) else "completed"
    await cache.set(_rk(run_id, "state"), final, ttl=_RUN_TTL)
    await cache.set(_rk(run_id, "finished_at"), _now_iso(), ttl=_RUN_TTL)
    await cache.set(_rk(run_id, "current"), None, ttl=_RUN_TTL)


# How many times ONE task re-runs a thread whose extraction came back INCOMPLETE
# (a model call failed, a message would not download) before reporting it
# failed. Each re-run re-reads only what is missing: everything already
# extracted is reused from the per-attachment record, not re-sent to the model.
_INCOMPLETE_ATTEMPTS = 3
_INCOMPLETE_BACKOFF = 20           # seconds; multiplied by the attempt number


async def run_one_thread(pmid: str, subject: str, run_id: str) -> None:
    """The unit of work for ONE thread - what app.services.tasks.
    extract_one_thread_task actually runs. A fresh DB session, entirely
    independent of every other thread's task: one slow or failed extraction
    never blocks or poisons another's, and Celery's own worker pool is what
    provides the parallelism (no in-process loop serialising them).

    Outcome accounting is honest: a thread whose extraction is still INCOMPLETE
    after its attempts counts as FAILED (with the reason in last_error), never
    as succeeded. The run counters move exactly once per thread even if Celery
    redelivers the task."""
    import asyncio

    from app.core.database import SessionLocal
    from app.services.extract_email.thread_scope import prior_message_for_merge

    if await _stop_requested(run_id):
        if await _mark_done_once(run_id, pmid):
            await cache.incr(_rk(run_id, "stopped_early"), ttl=_RUN_TTL)
        await cache.delete(_queued_key(run_id, pmid))
        await _finish_if_done(run_id)
        return

    await cache.set(_rk(run_id, "current"),
                    {"thread_id": pmid, "subject": subject, "events": []}, ttl=_RUN_TTL)

    outcome, detail = "succeeded", ""
    async with SessionLocal() as db:
        row = (await db.execute(
            select(EmailMessage).where(EmailMessage.provider_message_id == pmid)
        )).scalar_one_or_none()
        if row is None:
            outcome, detail = "failed", f"{subject}: message no longer in the mailbox"
        else:
            try:
                prior = await prior_message_for_merge(db, row)
                result: dict = {}
                for attempt in range(1, _INCOMPLETE_ATTEMPTS + 1):
                    result = await _extract_with_live_progress(db, row, subject, prior, run_id)
                    if not (result or {}).get("incomplete"):
                        break
                    if attempt < _INCOMPLETE_ATTEMPTS:
                        await asyncio.sleep(_INCOMPLETE_BACKOFF * attempt)
                        await db.rollback()
                if (result or {}).get("incomplete"):
                    errs = (result or {}).get("errors") or []
                    outcome = "failed"
                    detail = (f"{subject}: incomplete - "
                              f"{errs[0] if errs else 'a part of the thread could not be read'}")
            except Exception as e:
                outcome, detail = "failed", f"{subject}: {str(e)[:180]}"
        from app.core import datacache
        await datacache.bust_pipeline()

    if await _mark_done_once(run_id, pmid):
        await cache.incr(_rk(run_id, outcome), ttl=_RUN_TTL)
        if detail:
            await cache.set(_rk(run_id, "last_error"), detail[:240], ttl=_RUN_TTL)
    await cache.delete(_queued_key(run_id, pmid))
    await _finish_if_done(run_id)


async def _extract_with_live_progress(db, row: EmailMessage, subject: str, prior, run_id: str) -> dict:
    """Run extract_full_email through the SAME ProgressSink mechanism the
    manual "Extract Email" SSE stream uses (see streaming.py/progress.py) —
    mirrored into this run's `current` key instead of an HTTP response, so a
    browser polling /auto-extract/status sees the identical live
    unpack/pass1/pass2/batch animation Extract Email shows when run by hand."""
    import asyncio

    from app.services.agents.full_email_extract import extract_full_email
    from app.services.extract_email.progress import ProgressSink, reset_sink, set_sink

    sink = ProgressSink()
    events: list[dict] = []

    async def _drain() -> None:
        while True:
            event = await sink.queue.get()
            if event is None:
                break
            events.append(event)
            await cache.set(_rk(run_id, "current"),
                            {"thread_id": row.provider_message_id, "subject": subject, "events": events},
                            ttl=_RUN_TTL)

    token = set_sink(sink)
    drain_task = asyncio.create_task(_drain())
    try:
        return await extract_full_email(db, row, prior_email=prior)
    finally:
        reset_sink(token)
        sink.close()
        await drain_task
