"""Auto Extract (bulk background run) — progress must be reported one thread
at a time, in real time, with `processed`/`succeeded`/`failed`/`skipped`/
`current` always internally consistent. The per-thread pipeline itself
(extract_full_email) is stubbed out here — it's covered by
test_full_email_extract.py — so these tests isolate the
orchestration/bookkeeping in auto_extract.run_auto_extract."""
import asyncio
import datetime as dt
from contextlib import asynccontextmanager

from sqlalchemy import delete

from app.core.cache import cache
from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.services.extract_email import auto_extract, progress, sheet_cache


async def _never_extracted_bulk(db, thread_keys):
    """sheet_cache.last_extraction_at_bulk stand-in: nothing on record for any
    thread, so every anchor falls through to a real (stubbed) extraction —
    these tests are about THAT path, not the new skip check (see
    test_already_extracted_threads_are_skipped_without_touching_the_model
    below for that one)."""
    return {}


@asynccontextmanager
async def _seeded_threads(pmids: list[str]):
    """Insert throwaway EmailMessage rows for the run, then remove them —
    this suite shares one DB with the rest of the session, so leaking rows
    here would shift what other tests' /inbox queries see."""
    async with SessionLocal() as db:
        for pmid in pmids:
            db.add(EmailMessage(
                provider_message_id=pmid, sender_name="S", sender_email="s@x.y",
                subject=f"Subject {pmid}", body_text="", attachments=[],
                to_recipients=[], cc_recipients=[],
            ))
        await db.commit()
    try:
        yield
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.in_(pmids)))
            await db.commit()


async def _poll_until_done(snapshots: list[dict]) -> None:
    while True:
        s = await auto_extract.get_status()
        snapshots.append(dict(s))
        if s["state"] in ("completed", "stopped"):
            break
        await asyncio.sleep(0.005)


async def test_progress_is_reported_one_thread_at_a_time_and_stays_accurate(monkeypatch):
    pmids = ["AE-1", "AE-2", "AE-3"]
    anchors = [(p, f"Subject {p}", p, None) for p in pmids]
    fail_pmid = "AE-2"

    async def _fake_anchors(db):
        return anchors

    async def _fake_prior(db, row):
        return None

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
    monkeypatch.setattr(sheet_cache, "last_extraction_at_bulk", _never_extracted_bulk)
    monkeypatch.setattr(
        "app.services.extract_email.thread_scope.prior_message_for_merge", _fake_prior)

    async def _fake_extract(db, row, *, prior_email=None):
        await asyncio.sleep(0.03)  # give the concurrent poller a chance to observe this step
        if row.provider_message_id == fail_pmid:
            raise RuntimeError("boom")
        return {}

    monkeypatch.setattr("app.services.agents.full_email_extract.extract_full_email", _fake_extract)

    await cache.delete("auto_extract:status", "auto_extract:stop")

    async with _seeded_threads(pmids):
        snapshots: list[dict] = []
        run_task = asyncio.create_task(auto_extract.run_auto_extract())
        poll_task = asyncio.create_task(_poll_until_done(snapshots))
        await asyncio.gather(run_task, poll_task)

    final = snapshots[-1]
    assert final["state"] == "completed"
    assert final["total"] == 3
    assert final["processed"] == 3
    assert final["succeeded"] == 2
    assert final["failed"] == 1
    assert final["current"] is None

    # processed must climb 0 -> 1 -> 2 -> 3, one step at a time, never
    # skipping straight to the end and never exceeding total.
    processed_seq = [s["processed"] for s in snapshots if s["state"] != "idle"]
    assert processed_seq == sorted(processed_seq)
    assert sorted(set(processed_seq)) == [0, 1, 2, 3]

    # succeeded+failed must equal processed at EVERY observed instant, not
    # just at the end — a live viewer should never see inconsistent counts.
    for s in snapshots:
        if s["state"] == "idle":
            continue
        assert s["succeeded"] + s["failed"] == s["processed"], s

    # `current` must never still point at a thread that's already been
    # counted into `processed` — it should be cleared the instant that
    # thread's own count lands, not leak into the next thread's turn.
    seen_current_pmids = {
        s["current"]["thread_id"] for s in snapshots if s.get("current")
    }
    assert seen_current_pmids <= set(pmids)


async def test_already_extracted_threads_are_skipped_without_touching_the_model(monkeypatch):
    """Regression: a thread already extracted at/after its newest message
    (the normal "Extracted" state shown in the Inbox) was still being sent
    through extract_full_email on every bulk run — a real, paid model call
    for a thread with nothing new to read. It must now be skipped outright:
    no extract_full_email call, no succeeded/failed, counted as `skipped`."""
    now = dt.datetime(2026, 7, 29, tzinfo=dt.timezone.utc)
    earlier = now - dt.timedelta(days=1)
    later = now + dt.timedelta(days=1)

    anchors = [
        ("AE-done", "Already extracted", "AE-done", earlier),   # last_at (now) >= earlier -> skip
        ("AE-new", "Genuinely new reply", "AE-new", later),      # last_at (now) < later -> must run
        ("AE-never", "Never touched", "AE-never", now),         # no watermark at all -> must run
    ]

    async def _fake_anchors(db):
        return anchors

    async def _fake_prior(db, row):
        return None

    async def _fake_last_extraction_at_bulk(db, thread_keys):
        # "AE-never" has no watermark at all, so a real bulk query would
        # never return a row for it — a miss, not an explicit None.
        full = {"AE-done": now, "AE-new": now}
        return {tk: full[tk] for tk in thread_keys if tk in full}

    extracted_pmids: list[str] = []

    async def _fake_extract(db, row, *, prior_email=None):
        extracted_pmids.append(row.provider_message_id)
        return {}

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
    monkeypatch.setattr(sheet_cache, "last_extraction_at_bulk", _fake_last_extraction_at_bulk)
    monkeypatch.setattr(
        "app.services.extract_email.thread_scope.prior_message_for_merge", _fake_prior)
    monkeypatch.setattr("app.services.agents.full_email_extract.extract_full_email", _fake_extract)

    await cache.delete("auto_extract:status", "auto_extract:stop")

    pmids = [a[0] for a in anchors]
    async with _seeded_threads(pmids):
        final = await auto_extract.run_auto_extract()

    assert final["state"] == "completed"
    assert final["total"] == 3
    assert final["processed"] == 3
    assert final["skipped"] == 1
    assert final["succeeded"] == 2
    assert final["failed"] == 0
    # The already-extracted thread's model call never happened at all.
    assert extracted_pmids == ["AE-new", "AE-never"]


async def test_live_progress_events_from_the_pipeline_are_mirrored_into_current_status(monkeypatch):
    """The currently-processing thread must show the SAME live pass1/pass2
    progress the manual Extract Email stream shows, not just a static
    subject line — extract_full_email's ordinary progress.emit() calls
    (a no-op everywhere else, unless something installed a ProgressSink)
    must be captured into current["events"] as they happen."""
    pmids = ["AE-live-1"]
    anchors = [(p, f"Subject {p}", p, None) for p in pmids]

    async def _fake_anchors(db):
        return anchors

    async def _fake_prior(db, row):
        return None

    async def _fake_extract(db, row, *, prior_email=None):
        progress.emit("unpack", "ok", "Reduced junk.")
        await asyncio.sleep(0.02)
        progress.emit("pass1", "spin", "Pass 1 running…")
        await asyncio.sleep(0.02)
        return {}

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
    monkeypatch.setattr(sheet_cache, "last_extraction_at_bulk", _never_extracted_bulk)
    monkeypatch.setattr(
        "app.services.extract_email.thread_scope.prior_message_for_merge", _fake_prior)
    monkeypatch.setattr("app.services.agents.full_email_extract.extract_full_email", _fake_extract)

    await cache.delete("auto_extract:status", "auto_extract:stop")

    async with _seeded_threads(pmids):
        snapshots: list[dict] = []
        run_task = asyncio.create_task(auto_extract.run_auto_extract())
        poll_task = asyncio.create_task(_poll_until_done(snapshots))
        await asyncio.gather(run_task, poll_task)

    seen_stages = set()
    for s in snapshots:
        cur = s.get("current")
        if cur:
            for e in cur.get("events", []):
                seen_stages.add(e["stage"])
    assert {"unpack", "pass1"} <= seen_stages, snapshots

    final = snapshots[-1]
    assert final["current"] is None
    assert final["succeeded"] == 1


async def test_already_done_threads_are_counted_immediately_then_new_mail_runs(monkeypatch):
    """Watch-mode re-trigger on 1 new mail in a large already-done mailbox
    must NOT walk skip counters one-by-one (UI looking like 414/500). Skips
    are booked in one shot, then real extracts run newest-first."""
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

    async def _fake_prior(db, row):
        return None

    async def _fake_last_extraction_at_bulk(db, thread_keys):
        full = {"AE-old-1": now, "AE-old-2": now}
        return {tk: full[tk] for tk in thread_keys if tk in full}

    async def _fake_extract(db, row, *, prior_email=None):
        await asyncio.sleep(0.03)  # slow enough for the poller to observe mid-flight
        return {}

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
    monkeypatch.setattr(sheet_cache, "last_extraction_at_bulk", _fake_last_extraction_at_bulk)
    monkeypatch.setattr(
        "app.services.extract_email.thread_scope.prior_message_for_merge", _fake_prior)
    monkeypatch.setattr("app.services.agents.full_email_extract.extract_full_email", _fake_extract)

    await cache.delete("auto_extract:status", "auto_extract:stop")

    pmids = [a[0] for a in anchors]
    async with _seeded_threads(pmids):
        snapshots: list[dict] = []
        run_task = asyncio.create_task(auto_extract.run_auto_extract())
        poll_task = asyncio.create_task(_poll_until_done(snapshots))
        await asyncio.gather(run_task, poll_task)

    # Skips land before any real extract is counted — and the first current
    # thread is genuine new mail, not a backlog skip walking the counter.
    saw_running = False
    for s in snapshots:
        if s["state"] == "idle":
            continue
        if s["total"] == 4:
            saw_running = True
            assert s["skipped"] == 2, s
        if s.get("current"):
            assert s["current"]["thread_id"] in ("AE-real-1", "AE-real-2"), s
            assert s["skipped"] == 2, s
    assert saw_running

    final = snapshots[-1]
    assert final["skipped"] == 2
    assert final["succeeded"] == 2


async def test_stop_is_accurate_mid_run(monkeypatch):
    pmids = ["AE-10", "AE-11", "AE-12"]
    anchors = [(p, f"Subject {p}", p, None) for p in pmids]

    async def _fake_anchors(db):
        return anchors

    async def _fake_prior(db, row):
        return None

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
    monkeypatch.setattr(sheet_cache, "last_extraction_at_bulk", _never_extracted_bulk)
    monkeypatch.setattr(
        "app.services.extract_email.thread_scope.prior_message_for_merge", _fake_prior)

    started = asyncio.Event()

    async def _fake_extract(db, row, *, prior_email=None):
        started.set()
        await asyncio.sleep(0.05)
        return {}

    monkeypatch.setattr("app.services.agents.full_email_extract.extract_full_email", _fake_extract)

    await cache.delete("auto_extract:status", "auto_extract:stop")

    async with _seeded_threads(pmids):
        run_task = asyncio.create_task(auto_extract.run_auto_extract())

        # Ask it to stop WHILE the first thread is still in flight — it must
        # finish that one thread cleanly (never abandon it mid-write) and stop
        # before starting a second, rather than racing ahead.
        await asyncio.wait_for(started.wait(), timeout=2)
        await auto_extract.request_stop()

        final = await run_task
    assert final["state"] == "stopped"
    assert final["current"] is None
    # Never claims to have processed more than it actually could have, and
    # never fabricates a total that doesn't match the real queue.
    assert final["total"] == 3
    assert final["processed"] == 1
    assert final["succeeded"] + final["failed"] == final["processed"]


async def test_start_turns_on_the_persistent_enabled_flag_and_stop_turns_it_off(monkeypatch):
    """The `enabled` flag is what lets a later background sync tick decide
    whether to re-trigger a run on its own (see auto_extract's module
    docstring) — it must survive a run's own completion (still on
    afterward, since that's the whole point), and Stop must turn it off
    even when nothing is currently running, not only mid-run."""
    async def _fake_anchors(db):
        return []

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
    await cache.delete("auto_extract:status", "auto_extract:stop", "auto_extract:enabled")

    assert await auto_extract.is_enabled() is False

    started = await auto_extract.start()
    assert started["enabled"] is True

    # No anchors -> the run completes almost immediately. `enabled` is a
    # separate, longer-lived switch than this one run's lifecycle, so it
    # must still read True once the run is done.
    final = await auto_extract.get_status()
    assert final["state"] == "completed"
    assert final["enabled"] is True
    assert await auto_extract.is_enabled() is True

    stopped = await auto_extract.request_stop()
    assert stopped["enabled"] is False
    assert await auto_extract.is_enabled() is False

    await cache.delete("auto_extract:status", "auto_extract:stop", "auto_extract:enabled")


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
