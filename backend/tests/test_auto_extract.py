"""Auto Extract (per-thread background work) — progress must stay internally
consistent (succeeded+failed+skipped+stopped_early == processed, never
exceeding total) whether one thread finishes or several finish at once, the
skip check must recognise BOTH "already extracted" and "already checked,
found nothing" as already-done, and Stop must be race-free.

The per-thread pipeline itself (extract_full_email) is stubbed out here —
covered by test_full_email_extract.py — so these tests isolate the
orchestration/bookkeeping in auto_extract.py. Unlike the old design (one
sequential coroutine looping through every thread), work is now dispatched
as independent Celery tasks (auto_extract._dispatch -> extract_one_thread_task
.delay(...)), so these tests drive the underlying primitives directly
(_partition_needs_extraction, run_one_thread, _scan_and_dispatch,
enqueue_new_threads) rather than a single top-level coroutine — that's the
actual point of the redesign: no single call you can just await-and-poll
represents "the whole run" anymore, many independent tasks do."""
import asyncio
import datetime as dt

from sqlalchemy import delete, select

from app.core.cache import cache
from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.services.extract_email import auto_extract, progress, sheet_cache


async def _never_extracted_bulk(db, thread_keys):
    return {}


async def _fake_prior(db, row):
    return None


async def _cleanup_run(run_id: str) -> None:
    for field in ("state", "total", "skipped", "succeeded", "failed", "stopped_early",
                  "started_at", "finished_at", "last_error", "current", "stop"):
        await cache.delete(f"auto_extract:run:{run_id}:{field}")


async def _seed(pmids: list[str]) -> None:
    async with SessionLocal() as db:
        for pmid in pmids:
            db.add(EmailMessage(
                provider_message_id=pmid, sender_name="S", sender_email="s@x.y",
                subject=f"Subject {pmid}", body_text="", attachments=[],
                to_recipients=[], cc_recipients=[],
            ))
        await db.commit()


async def _unseed(pmids: list[str]) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.in_(pmids)))
        await db.commit()


async def test_already_extracted_threads_are_skipped_without_touching_the_model(monkeypatch):
    """Regression: a thread already extracted at/after its newest message
    (the normal "Extracted" state shown in the Inbox) must be skipped
    outright — no extract_full_email call."""
    now = dt.datetime(2026, 7, 29, tzinfo=dt.timezone.utc)
    earlier = now - dt.timedelta(days=1)
    later = now + dt.timedelta(days=1)

    anchors = [
        ("AE-done", "Already extracted", "AE-done", earlier),
        ("AE-new", "Genuinely new reply", "AE-new", later),
        ("AE-never", "Never touched", "AE-never", now),
    ]

    async def _fake_last_extraction_at_bulk(db, thread_keys):
        full = {"AE-done": now, "AE-new": now}
        return {tk: full[tk] for tk in thread_keys if tk in full}

    monkeypatch.setattr(sheet_cache, "last_extraction_at_bulk", _fake_last_extraction_at_bulk)

    pmids = [a[0] for a in anchors]
    await _seed(pmids)
    try:
        async with SessionLocal() as db:
            skip_now, need_real = await auto_extract._partition_needs_extraction(db, anchors)
    finally:
        await _unseed(pmids)

    assert {a[0] for a in skip_now} == {"AE-done"}
    assert {a[0] for a in need_real} == {"AE-new", "AE-never"}


async def test_no_sheets_found_threads_are_also_skipped_not_reprocessed_forever(monkeypatch):
    """The actual bug fix: a thread genuinely checked and found to have NO
    timesheet (EmailMessage.no_sheets_found_at) previously had no durable
    marker at all in this skip check — only PipelineFile-backed extractions
    counted — so it got re-sent through the vision model on every single
    re-trigger, forever. It must now be recognised as already-checked the
    same way a successful extraction is, as long as nothing newer arrived."""
    now = dt.datetime(2026, 7, 29, tzinfo=dt.timezone.utc)
    earlier = now - dt.timedelta(days=1)
    later = now + dt.timedelta(days=1)

    anchors = [
        ("AE-empty-fresh", "Checked, nothing found", "AE-empty-fresh", earlier),
        ("AE-empty-stale", "Checked, but newer reply since", "AE-empty-stale", later),
    ]

    monkeypatch.setattr(sheet_cache, "last_extraction_at_bulk", _never_extracted_bulk)

    async with SessionLocal() as db:
        for pmid, checked_at in (("AE-empty-fresh", now), ("AE-empty-stale", now)):
            db.add(EmailMessage(
                provider_message_id=pmid, sender_name="S", sender_email="s@x.y",
                subject=f"Subject {pmid}", body_text="", attachments=[],
                to_recipients=[], cc_recipients=[], no_sheets_found_at=checked_at,
            ))
        await db.commit()
    try:
        async with SessionLocal() as db:
            skip_now, need_real = await auto_extract._partition_needs_extraction(db, anchors)
    finally:
        await _unseed([a[0] for a in anchors])

    # fresh: checked_at (now) >= newest_at (earlier) -> already checked, skip.
    assert {a[0] for a in skip_now} == {"AE-empty-fresh"}
    # stale: a newer reply (later) arrived AFTER we checked -> must run again.
    assert {a[0] for a in need_real} == {"AE-empty-stale"}


async def test_progress_stays_consistent_as_independent_tasks_complete(monkeypatch):
    """Several threads' extractions completing (in any order, including
    "simultaneously" from an external poller's view — that's the whole
    point of dispatching them as independent tasks) must never leave
    succeeded+failed+skipped+stopped_early inconsistent with processed, and
    processed must never exceed total."""
    pmids = ["AE-1", "AE-2", "AE-3"]
    fail_pmid = "AE-2"

    async def _fake_extract(db, row, *, prior_email=None):
        await asyncio.sleep(0.02)
        if row.provider_message_id == fail_pmid:
            raise RuntimeError("boom")
        return {}

    monkeypatch.setattr(
        "app.services.extract_email.thread_scope.prior_message_for_merge", _fake_prior)
    monkeypatch.setattr("app.services.agents.full_email_extract.extract_full_email", _fake_extract)

    run_id = await auto_extract._new_run(total=3, skipped=0)
    await _seed(pmids)
    try:
        snapshots: list[dict] = []

        async def _poll():
            while True:
                s = await auto_extract.get_status()
                snapshots.append(dict(s))
                if s["state"] in ("completed", "stopped"):
                    break
                await asyncio.sleep(0.005)

        # We're driving THIS run_id directly (not through the global
        # auto_extract:run_id pointer), so poll a run-scoped status snapshot
        # instead of get_status() for correctness during concurrent work —
        # get_status() only reflects whichever run_id is globally "current".
        await cache.set("auto_extract:run_id", run_id, ttl=60)
        poll_task = asyncio.create_task(_poll())
        await asyncio.gather(*(
            auto_extract.run_one_thread(pmid, f"Subject {pmid}", run_id) for pmid in pmids
        ))
        await poll_task
    finally:
        await _unseed(pmids)
        await _cleanup_run(run_id)
        await cache.delete("auto_extract:run_id")

    final = snapshots[-1]
    assert final["state"] == "completed"
    assert final["total"] == 3
    assert final["processed"] == 3
    assert final["succeeded"] == 2
    assert final["failed"] == 1
    assert final["current"] is None

    for s in snapshots:
        assert s["succeeded"] + s["failed"] + s["skipped"] == s["processed"], s
        assert s["processed"] <= s["total"], s


async def test_live_progress_events_from_the_pipeline_are_mirrored_into_current_status(monkeypatch):
    """The currently-processing thread must show the SAME live pass1/pass2
    progress the manual Extract Email stream shows — extract_full_email's
    ordinary progress.emit() calls must land in current["events"]."""
    pmid = "AE-live-1"

    async def _fake_extract(db, row, *, prior_email=None):
        progress.emit("unpack", "ok", "Reduced junk.")
        await asyncio.sleep(0.02)
        progress.emit("pass1", "spin", "Pass 1 running…")
        await asyncio.sleep(0.02)
        return {}

    monkeypatch.setattr(
        "app.services.extract_email.thread_scope.prior_message_for_merge", _fake_prior)
    monkeypatch.setattr("app.services.agents.full_email_extract.extract_full_email", _fake_extract)

    run_id = await auto_extract._new_run(total=1, skipped=0)
    await _seed([pmid])
    try:
        seen_stages: set[str] = set()

        async def _poll():
            while True:
                cur = await cache.get(f"auto_extract:run:{run_id}:current")
                if cur:
                    for e in cur.get("events", []):
                        seen_stages.add(e["stage"])
                state = await cache.get(f"auto_extract:run:{run_id}:state")
                if state in ("completed", "stopped"):
                    break
                await asyncio.sleep(0.005)

        poll_task = asyncio.create_task(_poll())
        await auto_extract.run_one_thread(pmid, "Subject", run_id)
        await poll_task

        assert {"unpack", "pass1"} <= seen_stages
        assert await cache.get(f"auto_extract:run:{run_id}:current") is None
        assert int(await cache.get(f"auto_extract:run:{run_id}:succeeded") or 0) == 1
    finally:
        await _unseed([pmid])
        await _cleanup_run(run_id)


async def test_scan_and_dispatch_books_skips_in_one_shot_before_any_real_extract(monkeypatch):
    """Watch-mode re-trigger on 1 new mail in a large already-done mailbox
    must NOT walk skip counters one-by-one — skips are booked in a single
    shot as soon as the scan finishes, before any real extraction even
    starts, exactly the property the old design's inline loop had to fake by
    pre-seeding `processed` before its first real iteration."""
    now = dt.datetime(2026, 7, 29, tzinfo=dt.timezone.utc)
    earlier = now - dt.timedelta(days=30)

    anchors = [
        ("AE-real-1", "Genuinely new #1", "AE-real-1", now),
        ("AE-old-1", "Backlog #1", "AE-old-1", earlier),
        ("AE-old-2", "Backlog #2", "AE-old-2", earlier),
        ("AE-real-2", "Genuinely new #2", "AE-real-2", now),
    ]

    async def _fake_anchors(db):
        return anchors

    async def _fake_last_extraction_at_bulk(db, thread_keys):
        full = {"AE-old-1": now, "AE-old-2": now}
        return {tk: full[tk] for tk in thread_keys if tk in full}

    dispatched: list[tuple[str, str, str]] = []

    def _fake_dispatch(pmid, subject, run_id):
        dispatched.append((pmid, subject, run_id))

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
    monkeypatch.setattr(sheet_cache, "last_extraction_at_bulk", _fake_last_extraction_at_bulk)
    monkeypatch.setattr(auto_extract, "_dispatch", _fake_dispatch)

    run_id = await auto_extract._new_run(total=0, skipped=0)
    pmids = [a[0] for a in anchors]
    await _seed(pmids)
    try:
        await auto_extract._scan_and_dispatch(run_id)
        total = int(await cache.get(f"auto_extract:run:{run_id}:total") or 0)
        skipped = int(await cache.get(f"auto_extract:run:{run_id}:skipped") or 0)
        assert total == 4
        assert skipped == 2
        assert {p for p, _, _ in dispatched} == {"AE-real-1", "AE-real-2"}
        assert all(rid == run_id for _, _, rid in dispatched)
    finally:
        await _unseed(pmids)
        await _cleanup_run(run_id)


async def test_stop_is_race_free_against_not_yet_started_tasks(monkeypatch):
    """A task that hasn't started its real extraction yet when Stop is
    requested must exit immediately without doing any work, and be counted
    as `stopped_early` (still part of `processed`) rather than left
    unaccounted for — the run must still be able to reach a terminal state
    even though not everything actually ran."""
    pmids = ["AE-10", "AE-11", "AE-12"]

    ran: list[str] = []

    async def _fake_extract(db, row, *, prior_email=None):
        ran.append(row.provider_message_id)
        return {}

    monkeypatch.setattr(
        "app.services.extract_email.thread_scope.prior_message_for_merge", _fake_prior)
    monkeypatch.setattr("app.services.agents.full_email_extract.extract_full_email", _fake_extract)

    run_id = await auto_extract._new_run(total=3, skipped=0)
    await _seed(pmids)
    try:
        # Stop requested before ANY of the three tasks have run — mirrors a
        # worker picking up queued-but-not-started tasks after Stop.
        await cache.set(f"auto_extract:run:{run_id}:stop", True, ttl=60)
        await cache.set(f"auto_extract:run:{run_id}:state", "stopping", ttl=60)

        await asyncio.gather(*(
            auto_extract.run_one_thread(pmid, f"Subject {pmid}", run_id) for pmid in pmids
        ))
    finally:
        await _unseed(pmids)

    assert ran == []  # none of them actually extracted anything
    state = await cache.get(f"auto_extract:run:{run_id}:state")
    succeeded = int(await cache.get(f"auto_extract:run:{run_id}:succeeded") or 0)
    failed = int(await cache.get(f"auto_extract:run:{run_id}:failed") or 0)
    stopped_early = int(await cache.get(f"auto_extract:run:{run_id}:stopped_early") or 0)
    assert state == "stopped"
    assert succeeded == 0 and failed == 0
    assert stopped_early == 3
    await _cleanup_run(run_id)


async def test_start_turns_on_the_persistent_enabled_flag_and_stop_turns_it_off(monkeypatch):
    """The `enabled` flag lets a later background sync tick decide whether
    to enqueue/extend a run on its own — it must survive a run's own
    completion, and Stop must turn it off even when nothing is running."""
    async def _fake_anchors(db):
        return []

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
    await cache.delete("auto_extract:run_id", "auto_extract:enabled")

    assert await auto_extract.is_enabled() is False

    started = await auto_extract.start()
    assert started["enabled"] is True

    # No anchors -> the dispatched scan completes almost immediately (Celery
    # is eager in tests, so start() has already run the scan by the time it
    # returns). `enabled` is a separate, longer-lived switch than this one
    # run's lifecycle, so it must still read True once the run is done.
    final = await auto_extract.get_status()
    assert final["state"] == "completed"
    assert final["enabled"] is True
    assert await auto_extract.is_enabled() is True

    stopped = await auto_extract.request_stop()
    assert stopped["enabled"] is False
    assert await auto_extract.is_enabled() is False

    await cache.delete("auto_extract:run_id", "auto_extract:enabled")


async def test_coverage_reports_live_extracted_vs_total_threads():
    """auto_extract.coverage() backs the Details panel's "X of Y extracted"
    summary — must count off the SAME PipelineFile tag the Inbox's own
    Extracted badge and the skip check both read, computed live rather
    than from a separately-maintained counter that could drift."""
    from app.models.pipeline_file import PipelineFile, PipelineStatus
    from app.services.extract_email.constants import TAG_PREFIX

    pmids = ["AE-cov-1", "AE-cov-2", "AE-cov-3"]
    async with SessionLocal() as db:
        before = await auto_extract.coverage(db)

        for pmid in pmids:
            db.add(EmailMessage(
                provider_message_id=pmid, sender_name="S", sender_email="s@x.y",
                subject=f"Subject {pmid}", body_text="", attachments=[],
                to_recipients=[], cc_recipients=[],
            ))
        # Only AE-cov-1 has a real extraction marker -- the other two are
        # brand new, never-extracted threads.
        db.add(PipelineFile(
            filename="x", source_kind="email", source_id="AE-cov-1",
            thread_key="AE-cov-1", attachment_id=f"{TAG_PREFIX}:covtest",
            status=PipelineStatus.NEEDS_REVIEW,
        ))
        await db.commit()

        try:
            after = await auto_extract.coverage(db)
            assert after["total_threads"] - before["total_threads"] == 3
            assert after["extracted_threads"] - before["extracted_threads"] == 1
        finally:
            await db.execute(delete(PipelineFile).where(PipelineFile.thread_key.in_(pmids)))
            await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.in_(pmids)))
            await db.commit()


async def test_enqueue_new_threads_never_rescans_the_whole_mailbox(monkeypatch):
    """The steady-state new-mail path must resolve ONLY the given rows'
    threads — never call the full-mailbox scan (_list_all_thread_anchors) —
    that's the actual point of the redesign: O(new mail), not O(mailbox)."""
    await auto_extract._set_enabled(True)
    pmid = "AE-incr-1"

    def _boom(db):
        raise AssertionError("enqueue_new_threads must not do a full-mailbox scan")

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _boom)

    dispatched: list[tuple[str, str, str]] = []
    monkeypatch.setattr(auto_extract, "_dispatch",
                        lambda pmid, subject, run_id: dispatched.append((pmid, subject, run_id)))

    await _seed([pmid])
    await cache.delete("auto_extract:run_id")
    try:
        async with SessionLocal() as db:
            msg = (await db.execute(
                select(EmailMessage).where(EmailMessage.provider_message_id == pmid)
            )).scalar_one()
            result = await auto_extract.enqueue_new_threads(db, [msg])
        assert result is not None
        assert len(dispatched) == 1
        assert dispatched[0][0] == pmid
    finally:
        run_id = await auto_extract._current_run_id()
        await _unseed([pmid])
        await cache.delete("auto_extract:enabled", "auto_extract:run_id")
        if run_id:
            await _cleanup_run(run_id)
