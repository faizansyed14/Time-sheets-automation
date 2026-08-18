"""Extract Email is keyed to the CONVERSATION, not one message.

The model reads the whole thread, so the unit of work is the thread. Two
consequences this locks down:

  * Re-extracting after a reply must UPDATE the existing review item, not add
    a second one for the same employee+month — and must not double-count the
    leave already on the record.
  * The stored evidence must be the whole thread. Filing only the clicked
    message leaves a record whose approval lives in a reply nobody can see
    from Compare & Fix or the vault.
"""
from email.message import EmailMessage as MimeMessage

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile
from app.models.timesheet_record import TimesheetRecord
from app.services.extract_email.constants import TAG_PREFIX
from app.services.extract_email.staging import stage_groups
from app.services.extract_email.thread_collect import build_thread_bundle

CONV = "conv-thread-keying-test"


def _msg(subject: str, body: str, attachment: tuple[str, bytes] | None = None) -> bytes:
    m = MimeMessage()
    m["Subject"] = subject
    m["From"] = "employee@alpha.ae"
    m["To"] = "timesheet@alpha.ae"
    m.set_content(body)
    if attachment:
        fn, payload = attachment
        m.add_attachment(payload, maintype="application", subtype="pdf", filename=fn)
    return m.as_bytes()


# --------------------------------------------------------------------------
# Thread evidence bundle
# --------------------------------------------------------------------------

def test_bundle_keeps_every_message_and_its_attachment():
    from app.services.extraction.eml_parser import parse_eml

    msgs = [
        ("msg 1", _msg("TIMESHEET June", "My sheet", ("sheet.pdf", b"%PDF-1.4 sheet"))),
        ("msg 2", _msg("RE: TIMESHEET June", "Approved — D. Shetty")),
    ]
    data, name = build_thread_bundle(msgs, "TIMESHEET June")
    assert name.endswith(".eml")

    parsed = parse_eml(data)
    nested = [a for a in parsed["attachments"] if a["content_type"] == "message/rfc822"]
    assert len(nested) == 2, "both messages must be preserved for the reviewer"
    assert all(a["size"] > 0 for a in nested)

    # The approval reply is IN the evidence — that is the whole point.
    import base64
    inner = [parse_eml(base64.b64decode(a["data_b64"])) for a in nested]
    bodies = " ".join((i["body_text"] or "") for i in inner)
    assert "Approved" in bodies
    # ...and so is the original attachment.
    names = [a["filename"] for i in inner for a in i["attachments"]]
    assert "sheet.pdf" in names


def test_single_message_thread_is_not_wrapped():
    """One message is already a complete .eml — an extra layer would just make
    the reviewer click through a wrapper."""
    raw = _msg("TIMESHEET June", "My sheet")
    data, _name = build_thread_bundle([("msg 1", raw)], "TIMESHEET June")
    assert data == raw


# --------------------------------------------------------------------------
# Idempotent re-extraction
# --------------------------------------------------------------------------

async def _employee(db) -> Employee:
    emp = (await db.execute(select(Employee).where(
        Employee.employee_id == "E9100001"))).scalar_one_or_none()
    if not emp:
        emp = Employee(employee_id="E9100001", name="Thread Test",
                       location="DXB", account_manager="Test Manager")
        db.add(emp)
        await db.commit()
        await db.refresh(emp)
    return emp


_ALL_BUCKETS = ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")


def _group(emp, buckets):
    return {
        "tag": f"{TAG_PREFIX}:threadtest",
        "employee_pk": emp.id, "name": emp.name, "employee_id": emp.employee_id,
        "note": "matched", "month": 6, "year": 2026,
        "buckets": {**{b: [] for b in _ALL_BUCKETS}, **buckets},
        "working_days": [], "weekend_days": [], "uncertain_days": [],
        "issues": [], "missing_days": [], "unaccounted_days": [], "days_covered_total": 0,
        "overlap_flags": [], "fold_notes": [],
        "sheets": [{
            "name": "sheet.pdf", "kind": "timesheet",
            "employee_name": emp.name, "employee_id": emp.employee_id,
            "month": 6, "year": 2026, "manager_signature": False,
            "approval_evidence": "", "text": "1-June-26 08:00 AM",
            "working_days": [], "weekend_days": [], "uncertain_days": [],
            "days_covered": 0, "period_type": "partial", "missing_days": [],
            **{b: [] for b in _ALL_BUCKETS}, **buckets,
        }],
    }


async def _cleanup(db, emp):
    for t in (await db.execute(select(PipelineFile).where(
            PipelineFile.thread_key == CONV))).scalars():
        await db.delete(t)
    for r in (await db.execute(select(TimesheetRecord).where(
            TimesheetRecord.matched_employee_pk == emp.id,
            TimesheetRecord.month == 6, TimesheetRecord.year == 2026))).scalars():
        await db.delete(r)
    await db.commit()


async def test_reextract_after_a_reply_updates_the_same_item():
    """A new reply must not produce a second review item for the same month."""
    async with SessionLocal() as db:
        emp = await _employee(db)
        await _cleanup(db, emp)

        common = dict(
            source_kind="email", thread_key=CONV, content_type="message/rfc822",
            approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "thread-single-call", "model": "gpt-4o", "calls": 1})

        first = await stage_groups(
            db, source_id="msg-1", raw_bytes=b"thread v1", raw_name="thread.eml",
            groups=[_group(emp, {"sick": ["2026-06-19"]})], **common)
        assert len(first) == 1
        first_id = first[0].id

        # A reply arrives; the thread is read again from the NEWER message.
        second = await stage_groups(
            db, source_id="msg-2", raw_bytes=b"thread v2 longer", raw_name="thread.eml",
            groups=[_group(emp, {"sick": ["2026-06-19"]})], **common)

        assert len(second) == 1
        assert second[0].id == first_id, "re-extraction must reuse the same review item"

        rows = (await db.execute(select(PipelineFile).where(
            PipelineFile.thread_key == CONV))).scalars().all()
        assert len(rows) == 1, f"one item per thread, got {len(rows)}"

        # It now points at the message that triggered the latest run, and the
        # stored evidence was replaced with the longer thread.
        assert rows[0].source_id == "msg-2"
        assert rows[0].size_bytes == len(b"thread v2 longer")

        await _cleanup(db, emp)


async def test_reextract_can_correct_a_filed_record_downward():
    """The record merges by source_key. Keyed to the MESSAGE, each re-run of a
    thread looked like a different sheet, so the buckets unioned — a re-read
    could add leave to a filed record but never remove it. Keyed to the THREAD,
    the second read REPLACES the first."""
    from app.services.pipeline.ingestion import _file_key, ingest_manual_entry

    async with SessionLocal() as db:
        emp = await _employee(db)
        await _cleanup(db, emp)

        buckets_first = {b: [] for b in
                         ("annual", "remote", "sick", "maternity", "unpaid",
                          "absent", "public_holiday")}
        buckets_first["sick"] = ["2026-06-18", "2026-06-19"]
        key = _file_key(CONV, f"{TAG_PREFIX}:threadtest", "thread.eml")

        rec, tracker = await ingest_manual_entry(
            db, employee_pk=emp.id, month=6, year=2026, buckets=buckets_first,
            source_key=key, source_filename="thread.eml")
        await db.delete(tracker)
        await db.commit()
        assert sorted(rec.sick_leave_dates) == ["2026-06-18", "2026-06-19"]

        # Re-read of the SAME thread now says only one sick day.
        buckets_second = dict(buckets_first)
        buckets_second["sick"] = ["2026-06-19"]
        rec2, tracker2 = await ingest_manual_entry(
            db, employee_pk=emp.id, month=6, year=2026, buckets=buckets_second,
            source_key=key, source_filename="thread.eml")
        await db.delete(tracker2)
        await db.commit()
        await db.refresh(rec2)

        assert sorted(rec2.sick_leave_dates) == ["2026-06-19"], (
            "same thread re-read must REPLACE its contribution, not union it — "
            f"got {rec2.sick_leave_dates}")

        await _cleanup(db, emp)


async def test_read_attachments_are_recorded_for_the_badge():
    """Every run re-reads everything — reusing a stored result made a bad read
    permanent (a MEDICAL day booked as ANNUAL survived re-extraction). What is
    kept is a RECORD of what has been looked at, driving the Extracted/New
    badge, and it has to survive an inbox resync which rewrites the row."""
    from app.services.inbox.sync import sync_message
    from app.models.email_message import EmailMessage
    from app.services.email_provider.base import ProviderMessage
    from app.services.extract_email import sheet_cache

    digest = sheet_cache.content_key(b"%PDF-1.4 the sheet")
    sheet = {"name": "sheet.pdf", "kind": "timesheet", "employee_id": "E1",
             "month": 6, "year": 2026, "buckets": {"sick": ["2026-06-19"]}}

    async with SessionLocal() as db:
        msg = ProviderMessage(
            message_id="thread-cache-msg-1", sender_name="E", sender_email="e@alpha.ae",
            subject="TIMESHEET June", received_at=None, body_text="hi",
            conversation_id=CONV)
        # received_at is non-null in the model; give it a real value.
        from datetime import datetime, timezone
        msg.received_at = datetime.now(timezone.utc)
        await sync_message(db, msg)
        await db.commit()

        await sheet_cache.remember(db, "thread-cache-msg-1", "gpt-4o", {digest: sheet})

        row = (await db.execute(select(EmailMessage).where(
            EmailMessage.provider_message_id == "thread-cache-msg-1"))).scalar_one()
        assert sheet_cache.extracted_filenames(row) == ["sheet.pdf"]

        # An inbox resync rewrites the row — the record must survive it, or
        # every attachment would flip back to "New" after a sync.
        await sync_message(db, msg)
        await db.commit()
        await db.refresh(row)
        assert sheet_cache.extracted_filenames(row) == ["sheet.pdf"], \
            "resync wiped the record of what had been read"

        await db.delete(row)
        await db.commit()


def test_nested_eml_container_recorded_for_inbox_badge():
    """Manager-batch style: outer attachment is an .eml; extract unwraps it.
    Inbox lists the OUTER .eml name — that name must be recorded for the
    Extracted badge, not only the inner PDF name."""
    from email.message import EmailMessage

    from app.services.extract_email.thread_extract import collect_thread

    inner = EmailMessage()
    inner["Subject"] = "June sheet - Shruti"
    inner["From"] = "mgr@alpha.ae"
    inner["To"] = "timesheet@alpha.ae"
    inner.set_content("Attached.")
    inner.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                         filename="Shruti_June.pdf")
    outer = EmailMessage()
    outer["Subject"] = "Batch June"
    outer["From"] = "mgr@alpha.ae"
    outer["To"] = "timesheet@alpha.ae"
    outer.set_content("Several sheets.")
    from email import message_from_bytes, policy
    outer.add_attachment(
        message_from_bytes(inner.as_bytes(), policy=policy.default),
        filename="June-2026 Timesheet Report - Shruti.eml",
    )

    th = collect_thread([("msg 1", outer.as_bytes())])
    assert any(it.name == "Shruti_June.pdf" for it in th.items), [
        it.name for it in th.items]
    names = [c["name"] for c in th.opened_containers]
    assert "June-2026 Timesheet Report - Shruti.eml" in names, names


async def test_remember_records_opened_eml_containers_for_badge():
    """badge_only container entries surface in extracted_filenames and must
    never be treated as reusable sheet answers."""
    from app.services.inbox.sync import sync_message
    from app.models.email_message import EmailMessage
    from app.services.email_provider.base import ProviderMessage
    from app.services.extract_email import sheet_cache
    from datetime import datetime, timezone

    pdf_digest = sheet_cache.content_key(b"%PDF-1.4 inner")
    eml_digest = sheet_cache.content_key(b"fake-eml-bytes")
    sheet = {"name": "Shruti_June.pdf", "kind": "timesheet", "employee_id": "E1",
             "month": 6, "year": 2026}

    async with SessionLocal() as db:
        msg = ProviderMessage(
            message_id="nested-eml-badge-1", sender_name="E", sender_email="e@alpha.ae",
            subject="Batch", received_at=datetime.now(timezone.utc), body_text="hi",
            conversation_id=CONV + "-nested")
        await sync_message(db, msg)
        await db.commit()

        await sheet_cache.remember(
            db, "nested-eml-badge-1", "gpt-4o",
            {pdf_digest: sheet},
            containers=[{"name": "June-2026 Timesheet Report - Shruti.eml",
                         "digest": eml_digest}],
        )
        row = (await db.execute(select(EmailMessage).where(
            EmailMessage.provider_message_id == "nested-eml-badge-1"))).scalar_one()
        names = sheet_cache.extracted_filenames(row)
        assert "Shruti_June.pdf" in names
        assert "June-2026 Timesheet Report - Shruti.eml" in names, names

        cached = await sheet_cache.thread_cached_sheets(db, CONV + "-nested")
        assert cached[eml_digest].get("badge_only") is True
        assert not cached[eml_digest].get("sheet")

        await db.delete(row)
        await db.commit()


async def test_reuse_is_explicit_and_opt_in_per_call():
    """Incremental reuse (see test_incremental_extraction.py) is deliberate and
    scoped to one parameter — extract_thread_sheets only reuses a sheet when a
    caller explicitly hands it a cache map; passing nothing (or {}) reads
    everything fresh, exactly like before incremental extraction existed."""
    import inspect

    from app.services.extract_email import thread_extract

    params = inspect.signature(thread_extract.extract_thread_sheets).parameters
    assert "cached_sheets" in params
    assert params["cached_sheets"].default is None


async def test_items_from_different_threads_stay_separate():
    async with SessionLocal() as db:
        emp = await _employee(db)
        await _cleanup(db, emp)
        common = dict(
            source_kind="email", content_type="message/rfc822",
            raw_bytes=b"x", raw_name="t.eml",
            approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "thread-single-call", "model": "gpt-4o", "calls": 1})

        a = await stage_groups(db, source_id="m1", thread_key=CONV,
                               groups=[_group(emp, {"sick": ["2026-06-19"]})], **common)
        b = await stage_groups(db, source_id="m2", thread_key=CONV + "-other",
                               groups=[_group(emp, {"sick": ["2026-06-19"]})], **common)
        assert a[0].id != b[0].id, "different conversations are different work"

        for t in (await db.execute(select(PipelineFile).where(
                PipelineFile.thread_key == CONV + "-other"))).scalars():
            await db.delete(t)
        await db.commit()
        await _cleanup(db, emp)
