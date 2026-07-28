"""Incremental thread extraction: a reply into an already-extracted thread only
costs a read of the new message(s) + a couple of prior ones for context —
everything else already extracted for the conversation is reused, never
silently dropped from the result.

Covers three layers: the windowing that decides which messages even get
fetched (thread_collect.collect_thread_emls), the thread-wide cache lookup
(sheet_cache), and the full stack together (email.extract_full_email) with the
vision model mocked so behaviour is deterministic.
"""
import datetime as dt
from email.message import EmailMessage as MimeMessage

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile
from app.services.email_provider.base import ProviderMessage


def _provider_message(mid: str, subject: str, received_at: dt.datetime,
                      conversation_id: str = "conv-window") -> ProviderMessage:
    return ProviderMessage(
        message_id=mid, sender_name="Sender", sender_email="sender@alpha.ae",
        subject=subject, received_at=received_at, body_text=f"body of {subject}",
        conversation_id=conversation_id)


class _FakeProvider:
    """Just enough of EmailProvider for collect_thread_emls' conversation path."""

    def __init__(self, msgs: list[ProviderMessage], raw_by_id: dict[str, bytes]):
        self._msgs = msgs
        self._raw = raw_by_id

    async def list_thread_messages(self, conversation_id):
        return self._msgs

    async def get_message_mime(self, message_id):
        return self._raw.get(message_id)


def _raw_eml(subject: str) -> bytes:
    m = MimeMessage()
    m["Subject"] = subject
    m["From"] = "sender@alpha.ae"
    m.set_content(f"body of {subject}")
    return m.as_bytes()


# --------------------------------------------------------------------------
# Windowing — thread_collect.collect_thread_emls
# --------------------------------------------------------------------------

async def test_since_trims_to_new_plus_context_messages():
    from app.services.extract_email.thread_collect import collect_thread_emls

    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    msgs = [_provider_message(f"m{i}", f"msg {i}", base + dt.timedelta(days=i))
            for i in range(1, 9)]   # m1..m8, one per day
    raw = {m.message_id: _raw_eml(m.subject) for m in msgs}
    provider = _FakeProvider(msgs, raw)

    class _Anchor:
        conversation_id = "conv-window"
        provider_message_id = "m8"
        subject = "msg 8"

    # Extraction last ran through m5 — m6, m7, m8 are new.
    since = base + dt.timedelta(days=5)
    fetched, notes = await collect_thread_emls(provider, _Anchor(), since=since)

    subjects = [label.split(" — ", 1)[1] if " — " in label else label for label in
                [lbl for lbl, _ in fetched]]
    # window_start = new_index(m6, idx 5) - context_messages(2) = 3 -> m4..m8
    assert subjects == ["msg 4", "msg 5", "msg 6", "msg 7", "msg 8"]
    assert any("already extracted" in n or "were NOT re-read" in n for n in notes)


async def test_since_none_sends_the_whole_thread_unchanged():
    from app.services.extract_email.thread_collect import collect_thread_emls

    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    msgs = [_provider_message(f"m{i}", f"msg {i}", base + dt.timedelta(days=i))
            for i in range(1, 5)]
    raw = {m.message_id: _raw_eml(m.subject) for m in msgs}
    provider = _FakeProvider(msgs, raw)

    class _Anchor:
        conversation_id = "conv-window"
        provider_message_id = "m4"
        subject = "msg 4"

    fetched, notes = await collect_thread_emls(provider, _Anchor(), since=None)
    assert len(fetched) == 4
    assert notes == []


async def test_since_with_nothing_newer_falls_back_to_full_thread():
    """A manual re-run with no new mail at all must not silently send nothing."""
    from app.services.extract_email.thread_collect import collect_thread_emls

    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    msgs = [_provider_message(f"m{i}", f"msg {i}", base + dt.timedelta(days=i))
            for i in range(1, 4)]
    raw = {m.message_id: _raw_eml(m.subject) for m in msgs}
    provider = _FakeProvider(msgs, raw)

    class _Anchor:
        conversation_id = "conv-window"
        provider_message_id = "m3"
        subject = "msg 3"

    since = base + dt.timedelta(days=10)   # after every message
    fetched, _notes = await collect_thread_emls(provider, _Anchor(), since=since)
    assert len(fetched) == 3


# --------------------------------------------------------------------------
# Thread-wide cache aggregation — sheet_cache.py
# --------------------------------------------------------------------------

async def _email_row(db, provider_message_id: str, conversation_id: str,
                     extracted_sheets: dict | None = None) -> EmailMessage:
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == provider_message_id))).scalar_one_or_none()
    if row is None:
        row = EmailMessage(
            provider_message_id=provider_message_id, conversation_id=conversation_id,
            sender_name="S", sender_email="s@x.y", subject="s", body_text="",
            attachments=[])
        db.add(row)
    row.extracted_sheets = extracted_sheets or {}
    await db.commit()
    await db.refresh(row)
    return row


async def test_thread_cached_sheets_unions_across_every_message_in_the_conversation():
    from app.services.extract_email import sheet_cache

    conv = "conv-cache-agg"
    async with SessionLocal() as db:
        await _email_row(db, "cache-msg-1", conv, {
            "digest-1": {"filename": "a.pdf", "at": "2026-01-01T00:00:00+00:00", "sheet": {"name": "a.pdf"}}})
        await _email_row(db, "cache-msg-2", conv, {
            "digest-2": {"filename": "b.pdf", "at": "2026-01-02T00:00:00+00:00", "sheet": {"name": "b.pdf"}}})

        merged = await sheet_cache.thread_cached_sheets(db, conv)
        assert set(merged) == {"digest-1", "digest-2"}
        assert merged["digest-1"]["sheet"]["name"] == "a.pdf"

        # cleanup
        for pmid in ("cache-msg-1", "cache-msg-2"):
            row = (await db.execute(select(EmailMessage).where(
                EmailMessage.provider_message_id == pmid))).scalar_one()
            await db.delete(row)
        await db.commit()


async def test_thread_cached_sheets_returns_empty_without_a_conversation():
    from app.services.extract_email import sheet_cache

    assert await sheet_cache.thread_cached_sheets(None, None) == {}
    assert await sheet_cache.thread_cached_sheets(None, "", []) == {}


async def test_last_extraction_at_reads_the_pipeline_tag(client, admin_token):
    from app.services.extract_email import sheet_cache
    from app.services.extract_email.constants import TAG_PREFIX

    thread_key = "conv-last-extract-at"
    async with SessionLocal() as db:
        assert await sheet_cache.last_extraction_at(db, thread_key) is None

        row = PipelineFile(
            filename="thread.eml", content_type="message/rfc822", size_bytes=10,
            source_kind="email", source_id="msg-x", thread_key=thread_key,
            attachment_id=f"{TAG_PREFIX}:abc123")
        db.add(row)
        await db.commit()

        at = await sheet_cache.last_extraction_at(db, thread_key)
        assert at is not None

        await db.delete(row)
        await db.commit()


async def test_last_extraction_at_returns_none_for_falsy_thread_key():
    from app.services.extract_email import sheet_cache

    async with SessionLocal() as db:
        assert await sheet_cache.last_extraction_at(db, None) is None
        assert await sheet_cache.last_extraction_at(db, "") is None


# --------------------------------------------------------------------------
# End-to-end: a reply into an already-extracted thread reuses what's already
# been found, and only pays for what's genuinely new.
# --------------------------------------------------------------------------

async def _employee(db) -> Employee:
    emp = (await db.execute(select(Employee).where(
        Employee.employee_id == "E-INCR-1"))).scalar_one_or_none()
    if not emp:
        emp = Employee(employee_id="E-INCR-1", name="Incremental Person",
                       location="DXB", account_manager="Test Manager")
        db.add(emp)
        await db.commit()
        await db.refresh(emp)
    return emp


async def _clean(db, emp):
    from app.models.timesheet_record import TimesheetRecord

    for r in (await db.execute(select(TimesheetRecord).where(
            TimesheetRecord.matched_employee_pk == emp.id,
            TimesheetRecord.month == 6, TimesheetRecord.year == 2026))).scalars():
        await db.delete(r)
    for t in (await db.execute(select(PipelineFile).where(
            PipelineFile.thread_key == "conv-incremental"))).scalars():
        await db.delete(t)
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.conversation_id == "conv-incremental"))).scalars().all()
    for r in row:
        await db.delete(r)
    await db.commit()


def _week_eml(subject: str, week_pdf_name: str | None) -> bytes:
    m = MimeMessage()
    m["Subject"] = subject
    m["From"] = "employee@alpha.ae"
    m.set_content("See attached." if week_pdf_name else "Approved, thanks.")
    if week_pdf_name:
        m.add_attachment(f"%PDF-1.4 {week_pdf_name}".encode(), maintype="application",
                         subtype="pdf", filename=week_pdf_name)
    return m.as_bytes()


async def test_reply_into_an_extracted_thread_reuses_earlier_weeks_and_stays_complete(
    mock_vision_calls, monkeypatch,
):
    """3 weekly sheets get extracted first (populating the cache). A 4th
    message (an approval reply, no attachment) arrives; re-extracting must
    NOT lose the first 3 weeks' leave data even though this run's window
    doesn't refetch those messages."""
    from app.services.extract_email.email import extract_full_email

    async with SessionLocal() as db:
        emp = await _employee(db)
        await _clean(db, emp)

        # Message timestamps are relative to real "now" (last_extraction_at
        # reads a genuine PipelineFile.updated_at, stamped at whatever moment
        # this test actually runs) — the LEAVE dates the sheets carry are a
        # separate, arbitrary calendar month and don't need to relate to it.
        now = dt.datetime.now(dt.timezone.utc)
        base = now - dt.timedelta(hours=1)
        weeks = [
            ("week1.pdf", ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04",
                           "2026-06-05", "2026-06-06", "2026-06-07"]),
            ("week2.pdf", ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11",
                           "2026-06-12", "2026-06-13", "2026-06-14"]),
            ("week3.pdf", ["2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18",
                           "2026-06-19", "2026-06-20", "2026-06-21"]),
        ]
        msgs = [_provider_message(f"incr-{i}", f"week {i} timesheet",
                                  base + dt.timedelta(minutes=i * 10), "conv-incremental")
               for i in range(1, 4)]
        raw = {m.message_id: _week_eml(m.subject, name) for m, (name, _dates) in zip(msgs, weeks)}

        # A minimal, self-contained fake provider for this one conversation —
        # avoids mutating the real (session-cached) mock provider, which
        # would leak into every other test that runs afterward.
        provider = _FakeProvider(msgs, raw)
        monkeypatch.setattr(
            "app.services.email_provider.get_email_provider", lambda: provider)

        anchor_row = EmailMessage(
            provider_message_id="incr-3", conversation_id="conv-incremental",
            sender_name="Employee", sender_email="employee@alpha.ae",
            subject="week 3 timesheet", received_at=msgs[2].received_at,
            body_text="See attached.", attachments=[])
        db.add(anchor_row)
        await db.commit()
        await db.refresh(anchor_row)

        def _pass1_item(name, dates):
            return {
                "source": name, "is_timesheet": True, "kind": "timesheet",
                "employee_name": "Incremental Person", "employee_id": "E-INCR-1",
                "period_hint": "June 2026", "evidence": f"{dates[0]} present",
                "manager_signature": False, "signature_evidence": "", "notes": "",
            }

        def _pass2_sheet(name, dates):
            return {
                "source": name, "employee_name": "Incremental Person",
                "employee_id": "E-INCR-1", "month": 6, "year": 2026,
                "days_covered": len(dates), "period_type": "week", "missing_days": [],
                "working_days": dates, "weekend_days": [], "uncertain_days": [],
                "annual": [], "remote": [], "sick": [], "maternity": [], "unpaid": [],
                "absent": [], "public_holiday": [], "notes": "",
            }

        # 3 messages x (1 attachment + 1 body) = 6 items — raise the pass-1
        # batch size so all 6 fit in ONE call instead of splitting across two
        # (this test is about incremental reuse, not batching, which has its
        # own dedicated tests).
        from app.core.config import settings
        monkeypatch.setattr(settings, "pass1_batch_size", 10)

        # ---- first run: extracts all 3 weeks fresh, populating the cache ----
        # One pass-1 call classifies every item across the whole thread; one
        # pass-2 call extracts all 3 confirmed sheets (3 images, well under
        # the per-call cap) — matches the real single-batch-per-pass shape.
        mock_vision_calls([
            {"thread_summary": "", "items": [_pass1_item(name, dates) for name, dates in weeks]},
            {"sheets": [_pass2_sheet(name, dates) for name, dates in weeks]},
        ])
        # Force a full read since nothing has been extracted yet — no `since`
        # trimming should apply on the very first run regardless.
        res1 = await extract_full_email(db, anchor_row, force_full=True)
        assert res1["groups"] == 1, res1["message"]
        all_first_run_dates = set()
        for wk_name, wk_dates in weeks:
            all_first_run_dates.update(wk_dates)
        staged1 = res1["staged"][0]
        assert set(staged1.extraction_meta["staged"]["working_days"]) >= all_first_run_dates

    # ---- a 4th message (approval reply, no attachment) arrives ----
    async with SessionLocal() as db:
        # Safely after run 1's last_extraction_at (stamped at real "now"
        # above) — this is what makes the windowing logic see it as new.
        msg4 = _provider_message("incr-4", "RE: week 3 timesheet",
                                 dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5),
                                 "conv-incremental")
        msgs.append(msg4)
        raw[msg4.message_id] = _week_eml(msg4.subject, None)

        reply_row = EmailMessage(
            provider_message_id="incr-4", conversation_id="conv-incremental",
            sender_name="Manager", sender_email="manager@alpha.ae",
            subject="RE: week 3 timesheet", received_at=msg4.received_at,
            body_text="Approved, thanks.", attachments=[])
        db.add(reply_row)
        await db.commit()
        await db.refresh(reply_row)

        # Only the NEW message needs a fresh read this time — its body has no
        # attachment, so pass 1 sees just the body item across the window.
        mock_vision_calls([{"thread_summary": "", "items": [{
            "source": "email body (message 1)", "is_timesheet": False, "kind": "approval",
            "evidence": "", "manager_signature": True,
            "signature_evidence": "Approved, thanks.", "notes": "",
        }]}])

        res2 = await extract_full_email(db, reply_row, force_full=False)
        assert res2["groups"] == 1
        staged2 = res2["staged"][0]
        # All 3 earlier weeks' dates are STILL present — nothing lost even
        # though this run's window never refetched weeks 1-3.
        assert set(staged2.extraction_meta["staged"]["working_days"]) >= all_first_run_dates
        # And the approval from the new reply was picked up.
        assert res2["approval"]["detected"] is True

        await _clean(db, emp)
