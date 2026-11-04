"""Reliability of the extraction pipeline: no silent failures, no duplicate work,
no duplicate records.

Covers, bottom-up: the Redis lock primitive, the per-conversation extraction
lock, truncated model replies, incomplete-run bookkeeping (watermark, "no
sheets" mark, stale-item deletion, attempt counting), per-employee approval,
the Graph retry helper, the checkpointed inbox sync and — end to end — two
simultaneous extractions of ONE conversation (manual click during an automatic
run) producing exactly one set of review items.
"""
import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.core.cache import cache
from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.models.pipeline_file import PipelineFile


# --------------------------------------------------------------------------
# Lock primitive + per-thread lock
# --------------------------------------------------------------------------

async def test_cache_lock_is_exclusive_and_owner_checked():
    key = "t:lock:excl"
    await cache.delete(key)
    assert await cache.acquire_lock(key, 30, "a") is True
    assert await cache.acquire_lock(key, 30, "b") is False        # held
    assert await cache.release_lock(key, "b") is False             # not the owner
    assert await cache.extend_lock(key, "b", 30) is False
    assert await cache.extend_lock(key, "a", 30) is True
    assert await cache.release_lock(key, "a") is True
    assert await cache.acquire_lock(key, 30, "b") is True          # free again
    await cache.release_lock(key, "b")


async def test_thread_lock_serialises_and_tells_the_waiter_it_waited(monkeypatch):
    from app.services.extract_email import thread_lock as tl

    monkeypatch.setattr(tl, "POLL_EVERY", 0.01)
    order: list[str] = []

    async def worker(name: str, hold: float):
        async with tl.thread_lock("conv-lock-test") as waited:
            order.append(f"{name}:start:{waited}")
            await asyncio.sleep(hold)
            order.append(f"{name}:end")

    await asyncio.gather(worker("first", 0.1), worker("second", 0.0))
    # Whoever got the lock first finishes before the other starts.
    starts = [o for o in order if ":start:" in o]
    assert len(starts) == 2
    first = order[0].split(":")[0]
    assert order[1] == f"{first}:end"
    assert starts[0].endswith("False") and starts[1].endswith("True")


async def test_thread_lock_gives_up_with_a_clear_error(monkeypatch):
    from app.services.extract_email import thread_lock as tl

    monkeypatch.setattr(tl, "POLL_EVERY", 0.01)
    async with tl.thread_lock("conv-busy"):
        with pytest.raises(tl.ThreadBusy):
            async with tl.thread_lock("conv-busy", max_wait=0.05):
                pass


async def test_lock_is_released_even_when_the_run_raises():
    from app.services.extract_email import thread_lock as tl

    with pytest.raises(RuntimeError):
        async with tl.thread_lock("conv-boom"):
            raise RuntimeError("extraction blew up")
    async with tl.thread_lock("conv-boom") as waited:
        assert waited is False


# --------------------------------------------------------------------------
# vision_client: truncation is detected, not retried blindly
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status=200, payload=None, text_="", headers=None):
        self.status_code = status
        self._payload = payload
        self.text = text_
        self.headers = headers or {}

    def json(self):
        return self._payload


def _patch_http(monkeypatch, replies: list):
    """Replace httpx.AsyncClient with one that returns scripted responses and
    records every request body."""
    from app.services.extraction import vision_client

    sent: list[dict] = []
    queue = list(replies)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent.append(json)
            return queue.pop(0)

    monkeypatch.setattr(vision_client.httpx, "AsyncClient", _Client)

    async def _no_sleep(_s):
        return None

    # Stub only vision_client's view of asyncio - patching the real module would
    # also freeze every other task's sleeps (e.g. a lock heartbeat).
    monkeypatch.setattr(vision_client, "asyncio", SimpleNamespace(sleep=_no_sleep))
    return sent


def _ok(content: str, finish: str = "stop"):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}, "finish_reason": finish}]})


async def test_chat_call_uses_the_requested_output_budget(monkeypatch):
    from app.services.extraction import vision_client

    sent = _patch_http(monkeypatch, [_ok('{"a": 1}')])
    out = await vision_client.chat_call("sys", [], "m", "k", max_tokens=32000)
    assert out == {"a": 1}
    assert sent[0]["max_tokens"] == 32000


async def test_chat_call_default_budget_is_unchanged_for_other_callers(monkeypatch):
    from app.services.extraction import vision_client

    sent = _patch_http(monkeypatch, [_ok('{"a": 1}')])
    await vision_client.chat_call("sys", [], "m", "k")
    assert sent[0]["max_tokens"] == vision_client._MAX_TOKENS


async def test_a_reply_cut_off_at_the_limit_raises_truncated_without_repeating_the_call(monkeypatch):
    from app.services.extraction import vision_client

    sent = _patch_http(monkeypatch, [_ok('{"sheets": [{"source": "[A1]", "wor', finish="length")])
    with pytest.raises(vision_client.TruncatedReply):
        await vision_client.chat_call("sys", [], "m", "k", max_tokens=32000)
    assert len(sent) == 1, "an identical call would only truncate again"


async def test_provider_rejecting_the_big_budget_falls_back_to_the_default(monkeypatch):
    from app.services.extraction import vision_client

    sent = _patch_http(monkeypatch, [
        _FakeResponse(400, text_="max_tokens is too large: 32000 > 16384"),
        _ok('{"ok": true}'),
    ])
    out = await vision_client.chat_call("sys", [], "m", "k", max_tokens=32000)
    assert out == {"ok": True}
    assert [s["max_tokens"] for s in sent] == [32000, vision_client._MAX_TOKENS]


async def test_truncated_pass2_batch_is_split_not_lost(mock_vision_calls, monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets
    from app.services.extraction.vision_client import TruncatedReply
    from tests.test_thread_extract import _mail, _p1_item, _p2_sheet

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    monkeypatch.setattr(settings, "pass2_max_sheets_per_call", 2)
    names = ["Person 1", "Person 2"]
    eml = _mail(plain="See attached.", attachments=[
        (f"sheet{i}.pdf", f"%PDF-1.4 fake sheet {i}".encode(), "application", "pdf") for i in (1, 2)])
    mock_vision_calls([
        {"thread_summary": "", "items": [_p1_item(f"[A{i}]", n, f"E{i}") for i, n in enumerate(names, 1)]},
        TruncatedReply("cut off at 32000 output tokens"),
        {"sheets": [_p2_sheet("[A1]", "Person 1", "E1")]},
        {"sheets": [_p2_sheet("[A2]", "Person 2", "E2")]},
    ])
    sheets, _a, _c, meta = await extract_thread_sheets([("msg 1", eml)])
    assert sorted(s["employee_name"] for s in sheets) == names
    assert not meta["errors"]


# --------------------------------------------------------------------------
# Incomplete runs: bookkeeping
# --------------------------------------------------------------------------

def _meta(incomplete: bool, attempts: int = 1) -> dict:
    return {"full_email_extract": {"incomplete": incomplete, "incomplete_attempts": attempts,
                                   "snapshot_at": "2026-06-01T10:00:00+00:00"}}


def test_open_incomplete_run_is_detected_until_attempts_run_out():
    from app.services.extract_email import sheet_cache as sc

    assert sc.is_open_incomplete(_meta(True, 1)) is True
    assert sc.is_open_incomplete(_meta(True, sc.MAX_INCOMPLETE_ATTEMPTS - 1)) is True
    assert sc.is_open_incomplete(_meta(True, sc.MAX_INCOMPLETE_ATTEMPTS)) is False   # stop looping
    assert sc.is_open_incomplete(_meta(False)) is False
    assert sc.is_open_incomplete(None) is False
    assert sc.is_open_incomplete({"full_email_extract": {"incomplete": True, "incomplete_attempts": "x"}}) is True


async def _thread_with_rows(db, key: str, metas: list[dict]):
    for i, m in enumerate(metas):
        db.add(PipelineFile(
            filename=f"x{i}", content_type="message/rfc822", size_bytes=1, source_kind="email",
            source_id=f"{key}-m", thread_key=key, attachment_id=f"__email_extract__:{key}{i}",
            extraction_meta=m))
    await db.commit()


async def _drop_thread(db, key: str):
    for t in (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == key))).scalars():
        await db.delete(t)
    await db.commit()


async def test_incomplete_thread_has_no_watermark_so_it_is_re_extracted_in_full():
    from app.services.extract_email import sheet_cache as sc

    async with SessionLocal() as db:
        await _drop_thread(db, "conv-wm-ok")
        await _drop_thread(db, "conv-wm-bad")
        await _thread_with_rows(db, "conv-wm-ok", [_meta(False)])
        await _thread_with_rows(db, "conv-wm-bad", [_meta(False), _meta(True, 1)])
        try:
            assert await sc.last_extraction_at(db, "conv-wm-ok") is not None
            assert await sc.last_extraction_at(db, "conv-wm-bad") is None
            bulk = await sc.last_extraction_at_bulk(db, ["conv-wm-ok", "conv-wm-bad"])
            assert "conv-wm-ok" in bulk and "conv-wm-bad" not in bulk
        finally:
            await _drop_thread(db, "conv-wm-ok")
            await _drop_thread(db, "conv-wm-bad")


# --------------------------------------------------------------------------
# Per-employee approval inside one multi-employee email
# --------------------------------------------------------------------------

def test_a_signature_on_one_employees_sheet_does_not_approve_another():
    from app.services.extract_email.staging import group_approval

    approval = {"detected": True, "where": "sheet", "evidence": "stamp", "source": "a.pdf",
                "detail": "Approval found: stamp"}
    signed = {"sheets": [{"name": "a.pdf", "manager_signature": True,
                          "approval_evidence": "stamp", "approval_named_only": False}]}
    unsigned = {"sheets": [{"name": "b.pdf", "manager_signature": False}]}
    assert group_approval(signed, approval, 2)["detected"] is True
    other = group_approval(unsigned, approval, 2)
    assert other["detected"] is False
    assert "another employee" in other["detail"]
    # One group: nothing to disambiguate, the thread-wide verdict stands.
    assert group_approval(unsigned, approval, 1)["detected"] is True


def test_a_conversation_level_approval_is_inherited_with_a_caution():
    from app.services.extract_email.staging import group_approval

    approval = {"detected": True, "where": "conversation", "evidence": "Approved",
                "detail": "Approval found: Approved"}
    g = {"sheets": [{"name": "b.pdf", "manager_signature": False}]}
    out = group_approval(g, approval, 3)
    assert out["detected"] is True and "confirm" in out["detail"]


# --------------------------------------------------------------------------
# Staging under failure
# --------------------------------------------------------------------------

def _group(tag: str, name: str, pk=None):
    day = "2026-06-01"
    return {
        "tag": tag, "employee_pk": pk, "name": name, "employee_id": None, "note": "",
        "month": 6, "year": 2026, "sheets": [{
            "name": "s.pdf", "kind": "timesheet", "employee_name": name, "employee_id": None,
            "month": 6, "year": 2026, "manager_signature": False, "working_days": [day],
            "weekend_days": [], "uncertain_days": [], "missing_days": [], "days_covered": 1,
            "period_type": "partial", "annual": [], "remote": [], "sick": [], "maternity": [],
            "unpaid": [], "absent": [], "public_holiday": [], "other": []}],
        "working_days": [day], "weekend_days": [],
        "buckets": {b: [] for b in ("annual", "remote", "sick", "maternity", "unpaid",
                                    "absent", "public_holiday", "other")},
        "uncertain_days": [], "issues": [], "missing_days": [], "unaccounted_days": [],
        "days_covered_total": 1, "period_types": ["partial"], "overlap_flags": [], "fold_notes": [],
    }


def _meta_for_run(**over) -> dict:
    return {"method": "t", "model": "m", **over}


async def _stage(db, key: str, groups: list[dict], **run_meta):
    from app.services.extract_email.staging import stage_groups

    return await stage_groups(
        db, source_kind="email", source_id=f"{key}-msg", raw_bytes=b"raw", raw_name="t.eml",
        content_type="message/rfc822", groups=groups,
        approval={"detected": False, "detail": "none"}, run_meta=_meta_for_run(**run_meta),
        thread_key=key)


async def test_an_incomplete_rerun_does_not_delete_previously_staged_items():
    key = "conv-stage-incomplete"
    async with SessionLocal() as db:
        await _drop_thread(db, key)
        try:
            await _stage(db, key, [_group("__email_extract__:aaa", "Alice"),
                                   _group("__email_extract__:bbb", "Bob")])
            # A later run lost Bob's sheet to a failed model call: it only produced
            # Alice and says so. Bob's item must survive.
            await _stage(db, key, [_group("__email_extract__:aaa", "Alice")],
                         incomplete=True, errors=["Pass 2: 'bob.pdf' could not be read (boom)."])
            rows = (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == key))).scalars().all()
            assert sorted(r.attachment_id for r in rows) == ["__email_extract__:aaa", "__email_extract__:bbb"]
            alice = next(r for r in rows if r.attachment_id.endswith("aaa"))
            fe = alice.extraction_meta["full_email_extract"]
            assert fe["incomplete"] is True and fe["incomplete_attempts"] == 1
            flags = alice.extraction_meta["staged"]["flags"]
            assert any("INCOMPLETE" in f for f in flags)
            assert alice.extraction_meta["auto_accept"]["accepted"] is False

            # ... and a COMPLETE run afterwards does clean up the stale item.
            await _stage(db, key, [_group("__email_extract__:aaa", "Alice")])
            rows = (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == key))).scalars().all()
            assert [r.attachment_id for r in rows] == ["__email_extract__:aaa"]
            assert rows[0].extraction_meta["full_email_extract"]["incomplete_attempts"] == 0
        finally:
            await _drop_thread(db, key)


async def test_incomplete_attempts_are_counted_across_runs():
    key = "conv-stage-attempts"
    async with SessionLocal() as db:
        await _drop_thread(db, key)
        try:
            for expected in (1, 2, 3):
                await _stage(db, key, [_group("__email_extract__:ccc", "Cara")],
                             incomplete=True, errors=["boom"])
                row = (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == key))).scalars().one()
                assert row.extraction_meta["full_email_extract"]["incomplete_attempts"] == expected
        finally:
            await _drop_thread(db, key)


async def test_unreadable_attachment_warning_is_shown_and_blocks_auto_accept():
    key = "conv-stage-warn"
    async with SessionLocal() as db:
        await _drop_thread(db, key)
        try:
            await _stage(db, key, [_group("__email_extract__:ddd", "Dan")],
                         warnings=["scan.pdf: no pages or text could be read from this file."])
            row = (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == key))).scalars().one()
            assert any("scan.pdf" in f for f in row.extraction_meta["staged"]["flags"])
            assert row.extraction_meta["auto_accept"]["accepted"] is False
        finally:
            await _drop_thread(db, key)


async def test_concurrent_staging_of_the_same_group_never_creates_two_rows():
    """Two sessions stage the SAME thread+group at the same moment (lock expired /
    Redis down). The partial unique index refuses the second insert and the
    loser adopts the winner's row - exactly one row remains."""
    key = "conv-stage-race"
    async with SessionLocal() as db:
        await _drop_thread(db, key)
        await db.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_files_email_review_item ON pipeline_files "
            "(source_kind, thread_key, attachment_id) WHERE source_kind = 'email' "
            "AND thread_key IS NOT NULL AND attachment_id LIKE '__email_extract__:%'"))
        await db.commit()
    try:
        async def run():
            async with SessionLocal() as s:
                await _stage(s, key, [_group("__email_extract__:eee", "Eve")])

        results = await asyncio.gather(run(), run(), run(), return_exceptions=True)
        assert not [r for r in results if isinstance(r, Exception)], results
        async with SessionLocal() as db:
            rows = (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == key))).scalars().all()
            assert len(rows) == 1
    finally:
        async with SessionLocal() as db:
            await _drop_thread(db, key)
            await db.execute(text("DROP INDEX IF EXISTS uq_pipeline_files_email_review_item"))
            await db.commit()


# --------------------------------------------------------------------------
# End to end through extract_full_email
# --------------------------------------------------------------------------

def _eml_with_pdf(subject: str, pdf_name: str) -> bytes:
    from email.message import EmailMessage as MimeMessage

    m = MimeMessage()
    m["Subject"] = subject
    m["From"] = "employee@alpha.ae"
    m.set_content("See attached.")
    m.add_attachment(f"%PDF-1.4 {pdf_name}".encode(), maintype="application", subtype="pdf",
                     filename=pdf_name)
    return m.as_bytes()


async def _seed_thread(db, key: str, n_msgs: int = 1):
    from app.services.email_provider.base import ProviderMessage

    base = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    msgs, raw, rows = [], {}, []
    for i in range(1, n_msgs + 1):
        mid = f"{key}-{i}"
        received = base + dt.timedelta(minutes=i)
        msgs.append(ProviderMessage(
            message_id=mid, sender_name="E", sender_email="employee@alpha.ae",
            subject=f"timesheet {i}", received_at=received, body_text="See attached.",
            conversation_id=key))
        raw[mid] = _eml_with_pdf(f"timesheet {i}", f"week{i}.pdf")
        row = EmailMessage(
            provider_message_id=mid, conversation_id=key, sender_name="E",
            sender_email="employee@alpha.ae", subject=f"timesheet {i}", received_at=received,
            body_text="See attached.", attachments=[])
        db.add(row)
        rows.append(row)
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return msgs, raw, rows


async def _cleanup_thread(key: str):
    async with SessionLocal() as db:
        for t in (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == key))).scalars():
            await db.delete(t)
        for r in (await db.execute(select(EmailMessage).where(EmailMessage.conversation_id == key))).scalars():
            await db.delete(r)
        await db.commit()


def _p1(src, name):
    return {"source": src, "is_timesheet": True, "kind": "timesheet", "employee_name": name,
            "employee_id": None, "period_hint": "June 2026", "evidence": "1-Jun present",
            "manager_signature": False, "signature_evidence": "", "notes": ""}


def _p2(src, name):
    return {"source": src, "employee_name": name, "employee_id": None, "month": 6, "year": 2026,
            "days_covered": 1, "period_type": "partial", "missing_days": [],
            "working_days": ["2026-06-01"], "weekend_days": [], "uncertain_days": [],
            "annual": [], "remote": [], "sick": [], "maternity": [], "unpaid": [], "absent": [],
            "public_holiday": [], "notes": ""}


async def test_two_simultaneous_extractions_of_one_conversation_do_one_read_and_one_set_of_rows(
        monkeypatch):
    """Auto Extract is mid-run on a conversation and the user clicks Extract on it:
    the second caller waits, then returns the first one's result instead of
    paying for a second read or staging a second set of items."""
    from app.core.config import settings
    from app.services.extract_email.email import extract_full_email
    from app.services.extract_email import thread_lock as tl
    from app.services.extraction import vision_client
    from app.services.extract_email import thread_prompt, triage_prompt
    from tests.test_incremental_extraction import _FakeProvider

    monkeypatch.setattr(tl, "POLL_EVERY", 0.01)
    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    key = "conv-race-e2e"
    await _cleanup_thread(key)
    async with SessionLocal() as db:
        msgs, raw, rows = await _seed_thread(db, key)
    provider = _FakeProvider(msgs, raw)
    monkeypatch.setattr("app.services.email_provider.get_email_provider", lambda: provider)

    calls: list[str] = []

    async def fake_chat(system_prompt, blocks, model, api_key, *, label="", **kw):
        calls.append(label)
        await asyncio.sleep(0.05)       # a model call takes time - lets the 2nd caller arrive
        if label.startswith("pass1"):
            return {"thread_summary": "", "items": [_p1("[A1]", "Race Person"),
                                                    {"source": "[A2]", "kind": "other"}]}
        return {"sheets": [_p2("[A1]", "Race Person")]}

    for mod in (vision_client, triage_prompt, thread_prompt):
        monkeypatch.setattr(mod, "chat_call", fake_chat)

    async def run_one():
        async with SessionLocal() as s:
            row = (await s.execute(select(EmailMessage).where(
                EmailMessage.provider_message_id == f"{key}-1"))).scalar_one()
            return await extract_full_email(s, row)

    try:
        a, b = await asyncio.gather(run_one(), run_one())
        assert a["groups"] == 1 and b["groups"] == 1
        # one pass-1 call + one pass-2 call - the second caller did NOT read again
        assert len(calls) == 2, calls
        async with SessionLocal() as db:
            items = (await db.execute(select(PipelineFile).where(
                PipelineFile.thread_key == key,
                PipelineFile.attachment_id.like("__email_extract__%")))).scalars().all()
            assert len(items) == 1
    finally:
        await _cleanup_thread(key)


async def test_incomplete_extraction_is_not_marked_as_no_sheets_and_says_so(monkeypatch):
    """The model failing on the only attachment must NOT leave the email marked
    'no timesheet found' (which would stop every future scan from retrying)."""
    from app.core.config import settings
    from app.services.extract_email.email import extract_full_email
    from app.services.extraction import vision_client
    from app.services.extract_email import thread_prompt, triage_prompt
    from tests.test_incremental_extraction import _FakeProvider

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    key = "conv-incomplete-e2e"
    await _cleanup_thread(key)
    async with SessionLocal() as db:
        msgs, raw, rows = await _seed_thread(db, key)
    monkeypatch.setattr("app.services.email_provider.get_email_provider",
                        lambda: _FakeProvider(msgs, raw))

    async def always_fails(*a, **k):
        raise RuntimeError("model is down")

    for mod in (vision_client, triage_prompt, thread_prompt):
        monkeypatch.setattr(mod, "chat_call", always_fails)

    try:
        async with SessionLocal() as db:
            row = (await db.execute(select(EmailMessage).where(
                EmailMessage.provider_message_id == f"{key}-1"))).scalar_one()
            res = await extract_full_email(db, row)
            assert res["incomplete"] is True
            assert "INCOMPLETE" in res["message"]
            assert res["errors"]
            await db.refresh(row)
            assert row.no_sheets_found_at is None
    finally:
        await _cleanup_thread(key)


async def test_a_message_that_cannot_be_downloaded_makes_the_run_incomplete(monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.email import extract_full_email
    from app.services.extraction import vision_client
    from app.services.extract_email import thread_prompt, triage_prompt
    from tests.test_incremental_extraction import _FakeProvider

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    key = "conv-nodownload-e2e"
    await _cleanup_thread(key)
    async with SessionLocal() as db:
        msgs, raw, rows = await _seed_thread(db, key, n_msgs=2)
    raw.pop(f"{key}-1")                      # message 1 can never be downloaded
    monkeypatch.setattr("app.services.email_provider.get_email_provider",
                        lambda: _FakeProvider(msgs, raw))

    async def fake_chat(system_prompt, blocks, model, api_key, *, label="", **kw):
        if label.startswith("pass1"):
            return {"thread_summary": "", "items": [_p1("[A1]", "Partial Person")]}
        return {"sheets": [_p2("[A1]", "Partial Person")]}

    for mod in (vision_client, triage_prompt, thread_prompt):
        monkeypatch.setattr(mod, "chat_call", fake_chat)

    monkeypatch.setattr("app.services.extract_email.thread_collect._FETCH_RETRY_DELAY", 0)

    try:
        async with SessionLocal() as db:
            row = (await db.execute(select(EmailMessage).where(
                EmailMessage.provider_message_id == f"{key}-2"))).scalar_one()
            res = await extract_full_email(db, row)
            assert res["incomplete"] is True
            assert any("timesheet 1" in e for e in res["errors"]), res["errors"]
            item = (await db.execute(select(PipelineFile).where(
                PipelineFile.thread_key == key))).scalars().first()
            assert item is not None
            assert item.extraction_meta["full_email_extract"]["incomplete"] is True
    finally:
        await _cleanup_thread(key)


# --------------------------------------------------------------------------
# Auto Extract accounting
# --------------------------------------------------------------------------

async def test_auto_extract_counts_an_incomplete_thread_as_failed_not_succeeded(monkeypatch):
    from app.services.extract_email import auto_extract as ae

    key = "conv-ae-incomplete"
    await _cleanup_thread(key)
    async with SessionLocal() as db:
        _msgs, _raw, rows = await _seed_thread(db, key)
    pmid = rows[0].provider_message_id

    async def fake_live(db, row, subject, prior, run_id):
        return {"incomplete": True, "errors": ["Pass 2: 'x.pdf' could not be read (boom)."]}

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(ae, "_extract_with_live_progress", fake_live)
    monkeypatch.setattr(ae, "_INCOMPLETE_BACKOFF", 0)
    run_id = await ae._new_run(total=1, skipped=0)
    try:
        await ae.run_one_thread(pmid, "subj", run_id)
        st = await ae.get_status()
        assert st["failed"] == 1 and st["succeeded"] == 0
        assert "incomplete" in (st["last_error"] or "")
        assert st["state"] == "completed"
    finally:
        await _cleanup_thread(key)


async def test_auto_extract_retries_an_incomplete_thread_before_giving_up(monkeypatch):
    from app.services.extract_email import auto_extract as ae

    key = "conv-ae-retry"
    await _cleanup_thread(key)
    async with SessionLocal() as db:
        _msgs, _raw, rows = await _seed_thread(db, key)
    pmid = rows[0].provider_message_id
    attempts = {"n": 0}

    async def fake_live(db, row, subject, prior, run_id):
        attempts["n"] += 1
        return {"incomplete": attempts["n"] < 2, "errors": ["transient"]}

    monkeypatch.setattr(ae, "_extract_with_live_progress", fake_live)
    monkeypatch.setattr(ae, "_INCOMPLETE_BACKOFF", 0)
    run_id = await ae._new_run(total=1, skipped=0)
    try:
        await ae.run_one_thread(pmid, "subj", run_id)
        st = await ae.get_status()
        assert attempts["n"] == 2
        assert st["succeeded"] == 1 and st["failed"] == 0
    finally:
        await _cleanup_thread(key)


async def test_a_redelivered_task_does_not_double_count(monkeypatch):
    from app.services.extract_email import auto_extract as ae

    key = "conv-ae-redeliver"
    await _cleanup_thread(key)
    async with SessionLocal() as db:
        _msgs, _raw, rows = await _seed_thread(db, key)
    pmid = rows[0].provider_message_id

    async def fake_live(db, row, subject, prior, run_id):
        return {"incomplete": False, "errors": []}

    monkeypatch.setattr(ae, "_extract_with_live_progress", fake_live)
    run_id = await ae._new_run(total=2, skipped=0)
    try:
        await ae.run_one_thread(pmid, "subj", run_id)
        await ae.run_one_thread(pmid, "subj", run_id)       # Celery redelivered it
        st = await ae.get_status()
        assert st["succeeded"] == 1
        assert st["state"] == "running"                      # 1 of 2 - not ended early
    finally:
        await _cleanup_thread(key)


async def test_a_thread_already_queued_for_the_run_is_not_queued_twice(monkeypatch):
    from app.services.extract_email import auto_extract as ae

    sent: list[str] = []
    monkeypatch.setattr(ae, "_dispatch", lambda pmid, subject, run_id: sent.append(pmid))
    run_id = await ae._new_run(total=3, skipped=0)
    anchors = [("p1", "s", "t1", None), ("p2", "s", "t2", None)]
    assert await ae._dispatch_many(anchors, run_id) == 0
    assert await ae._dispatch_many(anchors + [("p3", "s", "t3", None)], run_id) == 2
    assert sent == ["p1", "p2", "p3"]


# --------------------------------------------------------------------------
# Graph: retry + no silent caps
# --------------------------------------------------------------------------

async def test_graph_get_retries_throttling_and_honours_retry_after(monkeypatch):
    from app.services.email_provider import graph_provider as gp

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    async def fake_headers(text_body=False):
        return {}

    monkeypatch.setattr(gp.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(gp, "_headers", fake_headers)
    replies = [SimpleNamespace(status_code=429, headers={"retry-after": "7"}, text="slow down"),
               SimpleNamespace(status_code=503, headers={}, text="busy"),
               SimpleNamespace(status_code=200, headers={}, text="ok")]

    class C:
        async def get(self, url, params=None, headers=None):
            return replies.pop(0)

    r = await gp._get(C(), "http://x")
    assert r.status_code == 200 and not replies
    assert sleeps[0] == 7.0 and len(sleeps) == 2


async def test_graph_get_gives_up_after_the_attempt_limit_and_returns_the_last_response(monkeypatch):
    from app.services.email_provider import graph_provider as gp

    async def fake_sleep(s):
        return None

    async def fake_headers(text_body=False):
        return {}

    monkeypatch.setattr(gp.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(gp, "_headers", fake_headers)
    n = {"c": 0}

    class C:
        async def get(self, url, params=None, headers=None):
            n["c"] += 1
            return SimpleNamespace(status_code=500, headers={}, text="boom")

    r = await gp._get(C(), "http://x")
    assert r.status_code == 500 and n["c"] == gp._MAX_ATTEMPTS


async def test_graph_mime_raises_instead_of_returning_none_on_a_persistent_error(monkeypatch):
    from app.services.email_provider import graph_provider as gp

    async def fake_get(c, url, **k):
        return SimpleNamespace(status_code=500, content=b"", text="err", headers={})

    monkeypatch.setattr(gp, "_get", fake_get)
    monkeypatch.setattr(gp.settings, "graph_mailbox", "m@x.y", raising=False)
    prov = gp.GraphEmailProvider.__new__(gp.GraphEmailProvider)
    with pytest.raises(RuntimeError):
        await prov.get_message_mime("mid")

    async def gone(c, url, **k):
        return SimpleNamespace(status_code=404, content=b"", text="", headers={})

    monkeypatch.setattr(gp, "_get", gone)
    assert await prov.get_message_mime("mid") is None        # genuinely deleted


async def test_graph_pages_are_not_capped_at_5000(monkeypatch):
    from app.services.email_provider import graph_provider as gp

    pages = 130            # 130 x 50 = 6500 messages, past the old 5000 cap

    def page(i):
        body = {"value": [{"id": f"m{i}-{j}", "subject": "s", "receivedDateTime": "2026-06-01T00:00:00Z",
                           "conversationId": "c", "from": {"emailAddress": {"address": "a@b.c", "name": "A"}},
                           "body": {"content": "x", "contentType": "text"}, "attachments": []}
                          for j in range(50)]}
        if i + 1 < pages:
            body["@odata.nextLink"] = f"http://next/{i + 1}"
        return body

    state = {"i": 0}

    async def fake_get(c, url, **k):
        i = state["i"]
        state["i"] += 1
        return SimpleNamespace(status_code=200, json=lambda: page(i), headers={}, text="")

    monkeypatch.setattr(gp, "_get", fake_get)
    monkeypatch.setattr(gp.settings, "graph_mailbox", "m@x.y", raising=False)
    prov = gp.GraphEmailProvider.__new__(gp.GraphEmailProvider)
    total = 0
    async for p in prov.iter_message_pages():
        total += len(p)
    assert total == pages * 50


# --------------------------------------------------------------------------
# Inbox sync: checkpointed + resumable
# --------------------------------------------------------------------------

def _pm(i: int, received: dt.datetime):
    from app.services.email_provider.base import ProviderMessage

    return ProviderMessage(
        message_id=f"sync-ck-{i}", sender_name="S", sender_email="s@alpha.ae", subject=f"m{i}",
        received_at=received, body_text="b", conversation_id=f"sync-ck-conv-{i}")


class _PagedProvider:
    """3 pages of 2 newest-first messages; page 2 blows up on the first attempt."""

    def __init__(self):
        base = dt.datetime(2026, 6, 10, tzinfo=dt.timezone.utc)
        self.pages = [[_pm(i, base - dt.timedelta(minutes=i)) for i in range(p * 2, p * 2 + 2)]
                      for p in range(3)]
        self.fail_page = 1
        self.calls: list[dict] = []

    async def iter_message_pages(self, since=None, before=None):
        self.calls.append({"since": since, "before": before})
        for n, page in enumerate(self.pages):
            if before is not None:
                page = [m for m in page if m.received_at < before]
                if not page:
                    continue
            if n == self.fail_page:
                self.fail_page = -1
                raise RuntimeError("Graph hiccup on page 2")
            yield page


async def _wipe_sync_rows():
    async with SessionLocal() as db:
        for r in (await db.execute(select(EmailMessage).where(
                EmailMessage.provider_message_id.like("sync-ck-%")))).scalars():
            await db.delete(r)
        await db.commit()


async def test_sync_commits_each_page_and_resumes_a_failed_backfill(monkeypatch):
    from app.services.inbox import sync as sync_mod

    await _wipe_sync_rows()
    await cache.delete(sync_mod.SYNC_LAST_KEY, sync_mod.SYNC_FRESH_KEY, sync_mod.SYNC_LOCK_KEY,
                       sync_mod.SYNC_BACKFILL_KEY)
    prov = _PagedProvider()
    monkeypatch.setattr(sync_mod, "get_email_provider", lambda: prov)
    # Pretend the table is "empty" check passes: no cursor -> a backfill (since=None).
    try:
        async with SessionLocal() as db:
            assert await sync_mod.sync_inbox(db) is None                 # failed on page 2
        async with SessionLocal() as db:
            stored = (await db.execute(select(EmailMessage.provider_message_id).where(
                EmailMessage.provider_message_id.like("sync-ck-%")))).scalars().all()
            assert sorted(stored) == ["sync-ck-0", "sync-ck-1"]          # page 1 survived
        assert await cache.get(sync_mod.SYNC_LAST_KEY) is None            # cursor did NOT advance
        assert await cache.get(sync_mod.SYNC_BACKFILL_KEY)                # resume point saved
        assert "Graph hiccup" in (await cache.get(sync_mod.SYNC_ERROR_KEY) or "")

        async with SessionLocal() as db:
            resumed = await sync_mod.sync_inbox(db)                      # resumed backfill completes
            assert sorted(r.provider_message_id for r in resumed) == [
                "sync-ck-2", "sync-ck-3", "sync-ck-4", "sync-ck-5"]
        async with SessionLocal() as db:
            stored = (await db.execute(select(EmailMessage.provider_message_id).where(
                EmailMessage.provider_message_id.like("sync-ck-%")))).scalars().all()
            assert len(stored) == 6
        assert prov.calls[1]["before"] is not None                        # resumed below page 1
        assert await cache.get(sync_mod.SYNC_LAST_KEY) is not None
        assert await cache.get(sync_mod.SYNC_BACKFILL_KEY) is None
    finally:
        await _wipe_sync_rows()
        await cache.delete(sync_mod.SYNC_LAST_KEY, sync_mod.SYNC_FRESH_KEY, sync_mod.SYNC_LOCK_KEY,
                           sync_mod.SYNC_BACKFILL_KEY, sync_mod.SYNC_ERROR_KEY)


# --------------------------------------------------------------------------
# Accept: one record per employee + month, even when Accepts collide
# --------------------------------------------------------------------------

async def test_two_simultaneous_accepts_for_one_employee_month_make_one_record(monkeypatch):
    from app.models.employee import Employee
    from app.models.timesheet_record import TimesheetRecord
    from app.services.extract_email import thread_lock as tl
    from app.services.pipeline.ingestion import ingest_manual_entry

    monkeypatch.setattr(tl, "POLL_EVERY", 0.01)
    async with SessionLocal() as db:
        emp = (await db.execute(select(Employee).where(Employee.employee_id == "E-ACC-RACE"))).scalar_one_or_none()
        if emp is None:
            emp = Employee(employee_id="E-ACC-RACE", name="Race Accept Person", location="DXB",
                           account_manager="Test Manager")
            db.add(emp)
            await db.commit()
            await db.refresh(emp)
        for r in (await db.execute(select(TimesheetRecord).where(
                TimesheetRecord.matched_employee_pk == emp.id))).scalars():
            await db.delete(r)
        await db.commit()
        pk = emp.id

    async def accept(key: str, day: str):
        async with SessionLocal() as s:
            await ingest_manual_entry(
                s, employee_pk=pk, month=5, year=2026,
                buckets={"annual": [day]}, source_key=key, source_filename=f"{key}.pdf")

    try:
        await asyncio.gather(accept("sheet-a", "2026-05-04"), accept("sheet-b", "2026-05-05"))
        async with SessionLocal() as db:
            recs = (await db.execute(select(TimesheetRecord).where(
                TimesheetRecord.matched_employee_pk == pk,
                TimesheetRecord.month == 5, TimesheetRecord.year == 2026))).scalars().all()
            assert len(recs) == 1, "two Accepts must MERGE into one monthly record"
            # and the second sheet's leave was unioned in, not lost
            assert sorted(recs[0].annual_leave_dates or []) == ["2026-05-04", "2026-05-05"]
    finally:
        async with SessionLocal() as db:
            for r in (await db.execute(select(TimesheetRecord).where(
                    TimesheetRecord.matched_employee_pk == pk))).scalars():
                await db.delete(r)
            await db.commit()


async def test_scan_and_anchor_lookups_survive_more_threads_than_the_driver_bind_limit():
    """A mailbox-wide scan asks about every thread at once. One IN (...) with more
    than 32 767 keys makes asyncpg refuse the query - the whole Auto Extract
    catch-up used to be unable to run on a large mailbox."""
    from app.services.extract_email import auto_extract as ae

    anchors = [(f"p{i}", "s", f"t{i}", None) for i in range(40_000)]
    async with SessionLocal() as db:
        skip, need = await ae._partition_needs_extraction(db, anchors)
        assert len(skip) == 0 and len(need) == 40_000
        assert await ae._anchors_for_pmids(db, [f"nope-{i}" for i in range(40_000)]) == []


def test_pass1_batches_respect_the_image_cap_and_keep_a_big_document_together():
    from app.services.extract_email.thread_extract import Item
    from app.services.extract_email.triage_prompt import chunk_items_by_count

    def item(n_images: int, i: int) -> Item:
        return Item(key=f"A{i}", name=f"f{i}", mime="x", msg_index=0, images=[b"x"] * n_images)

    items = [item(1, 1), item(1, 2), item(40, 3), item(1, 4), item(1, 5)]
    # count-only (old behaviour): everything in one request
    assert [len(b) for b in chunk_items_by_count(items, 5)] == [5]
    # with an image cap the 40-page document travels alone, all its pages together
    batches = chunk_items_by_count(items, 5, max_images=12)
    assert [[it.key for it in b] for b in batches] == [["A1", "A2"], ["A3"], ["A4", "A5"]]
    assert sum(len(b) for b in batches) == 5


async def test_a_no_sheets_mark_from_before_the_rules_improved_is_not_trusted(monkeypatch):
    """Threads concluded 'nothing here' under the old limits (small screenshots
    dropped, failed calls recorded as empty) must be looked at again once."""
    from app.core.config import settings
    from app.services.extract_email import auto_extract as ae

    key = "conv-stale-nosheets"
    await _cleanup_thread(key)
    async with SessionLocal() as db:
        _m, _r, rows = await _seed_thread(db, key)
        row = rows[0]
        newest = row.received_at
        row.no_sheets_found_at = newest + dt.timedelta(minutes=5)       # checked after the last mail
        await db.commit()
        anchor = (row.provider_message_id, "s", key, newest)
        try:
            monkeypatch.setattr(settings, "extract_rules_revision_at", "2000-01-01T00:00:00+00:00")
            skip, need = await ae._partition_needs_extraction(db, [anchor])
            assert len(skip) == 1 and not need                           # old mark still trusted

            monkeypatch.setattr(settings, "extract_rules_revision_at", "2099-01-01T00:00:00+00:00")
            skip, need = await ae._partition_needs_extraction(db, [anchor])
            assert len(need) == 1 and not skip                           # rules improved since: re-check
        finally:
            await _cleanup_thread(key)
