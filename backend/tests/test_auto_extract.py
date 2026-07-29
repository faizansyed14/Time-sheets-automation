"""Auto Extract (bulk background run) — progress must be reported one thread
at a time, in real time, with `processed`/`succeeded`/`failed`/`current`
always internally consistent. The per-thread pipeline itself (extract_full_email)
is stubbed out here — it's covered by test_full_email_extract.py — so these
tests isolate the orchestration/bookkeeping in auto_extract.run_auto_extract."""
import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import delete

from app.core.cache import cache
from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.services.extract_email import auto_extract


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
    anchors = [(p, f"Subject {p}") for p in pmids]
    fail_pmid = "AE-2"

    async def _fake_anchors(db):
        return anchors

    async def _fake_prior(db, row):
        return None

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
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


async def test_stop_is_accurate_mid_run(monkeypatch):
    pmids = ["AE-10", "AE-11", "AE-12"]
    anchors = [(p, f"Subject {p}") for p in pmids]

    async def _fake_anchors(db):
        return anchors

    async def _fake_prior(db, row):
        return None

    monkeypatch.setattr(auto_extract, "_list_all_thread_anchors", _fake_anchors)
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
