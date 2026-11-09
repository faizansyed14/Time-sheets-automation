"""A manager's "Approved." arrives days AFTER the timesheet was processed.

The reply has no attachment. It must not be read as "no sheets found": the sheet read
earlier is kept, the approval is attached to it, and - if the month was already
accepted and filed - the item comes back to review ONCE, saying an approval arrived, so
the record can be updated. Later harmless replies ("Thanks!") must not reopen it again.
"""
import datetime as dt
import re
from email.message import EmailMessage as Mime

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.models.pipeline_file import PipelineFile, PipelineStatus

KEY = "conv-late-approval"
BASE = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
BODIES = {1: ("week1.pdf", "My timesheet for week 1, please approve."),
          2: (None, "Approved."),
          3: (None, "Thanks!")}


def _eml(i):
    fname, body = BODIES[i]
    m = Mime()
    m["Subject"] = f"June timesheet ({i})"
    m["From"] = "someone@alpha.ae"
    m.set_content(body)
    if fname:
        m.add_attachment(f"%PDF-1.4 {fname}".encode(), maintype="application", subtype="pdf", filename=fname)
    return m.as_bytes()


def _model(monkeypatch):
    from app.core.config import settings
    from app.services.extract_email import thread_prompt, triage_prompt
    from app.services.extraction import vision_client

    monkeypatch.setattr(settings, "pass1_batch_size", 50)

    async def fake_chat(system_prompt, blocks, model, api_key, *, label="", **kw):
        text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        if label.startswith("alternates"):
            return {"items": [], "copy_groups": []}
        if label.startswith("pass1"):
            out = []
            for key, name, _n in re.findall(r"^\s+\[(A\d+)\] (.+?) · from message (\d+)", text, re.M):
                name = name.strip()
                if name.endswith(".pdf"):
                    out.append({"source": f"[{key}]", "is_timesheet": True, "kind": "timesheet",
                                "employee_name": "Late Person", "employee_id": None,
                                "period_hint": "June 2026", "evidence": "1-Jun present",
                                "manager_signature": False, "signature_evidence": "", "notes": ""})
                elif name.startswith("email body"):
                    k = re.search(r"\d+", name).group(0)
                    body = re.search(rf"--- message {k} \(depth \d+\) ---\n(.*?)(?=\n--- message|\Z)", text, re.S)
                    if body and "Approved." in body.group(1):
                        # the model sets manager_signature on an approval reply, as the prompt tells it to
                        out.append({"source": f"[{key}]", "kind": "approval", "manager_signature": True,
                                    "signature_evidence": "manager replied: Approved."})
                    else:
                        out.append({"source": f"[{key}]", "kind": "other"})
            return {"thread_summary": "", "items": out}
        sheets = []
        for key, name in re.findall(r"===== ITEM \[(A\d+)\] (.+?) =====", text):
            if name.strip().endswith(".pdf"):
                sheets.append({"source": f"[{key}]", "employee_name": "Late Person", "employee_id": None,
                               "month": 6, "year": 2026, "days_covered": 5, "period_type": "week",
                               "missing_days": [], "working_days": [f"2026-06-{d:02d}" for d in range(1, 6)],
                               "weekend_days": [], "uncertain_days": [], "annual": [], "remote": [],
                               "sick": [], "maternity": [], "unpaid": [], "absent": [],
                               "public_holiday": [], "notes": ""})
        return {"sheets": sheets}

    for mod in (vision_client, triage_prompt, thread_prompt):
        monkeypatch.setattr(mod, "chat_call", fake_chat)


async def _cleanup():
    async with SessionLocal() as db:
        for t in (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == KEY))).scalars():
            await db.delete(t)
        for r in (await db.execute(select(EmailMessage).where(EmailMessage.conversation_id == KEY))).scalars():
            await db.delete(r)
        await db.commit()


async def _item():
    async with SessionLocal() as db:
        return (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == KEY))).scalars().one()


async def _play(monkeypatch, *, filed: bool):
    from app.services.email_provider.base import ProviderMessage
    from app.services.extract_email import email as email_mod
    from app.services.extract_email.email import extract_full_email
    from tests.test_incremental_extraction import _FakeProvider

    _model(monkeypatch)
    await _cleanup()
    clock = {"now": BASE}

    class _Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(email_mod, "datetime", _Clock)
    msgs, raw = [], {}
    monkeypatch.setattr("app.services.email_provider.get_email_provider", lambda: _FakeProvider(msgs, raw))

    async def deliver(i, days_later):
        mid = f"{KEY}-{i}"
        received = BASE + dt.timedelta(days=days_later)
        msgs.append(ProviderMessage(message_id=mid, sender_name="S", sender_email="s@alpha.ae",
                                    subject=f"June timesheet ({i})", received_at=received,
                                    body_text=BODIES[i][1], conversation_id=KEY))
        raw[mid] = _eml(i)
        clock["now"] = received + dt.timedelta(minutes=30)
        async with SessionLocal() as db:
            db.add(EmailMessage(provider_message_id=mid, conversation_id=KEY, sender_name="S",
                                sender_email="s@alpha.ae", subject=f"June timesheet ({i})",
                                received_at=received, body_text=BODIES[i][1], attachments=[]))
            await db.commit()
            row = (await db.execute(select(EmailMessage).where(EmailMessage.provider_message_id == mid))).scalar_one()
            return await extract_full_email(db, row), row

    first, _ = await deliver(1, 0)
    assert first["groups"] == 1
    item = await _item()
    assert item.extraction_meta["full_email_extract"]["approval"]["detected"] is False
    if filed:                                      # the reviewer accepted it
        async with SessionLocal() as db:
            t = (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == KEY))).scalars().one()
            t.status, t.failure_code, t.record_id, t.raw_path = PipelineStatus.SUCCESS, None, "rec-1", None
            await db.commit()
    return deliver


async def test_a_late_approval_reply_is_not_no_sheets_and_updates_a_pending_item(monkeypatch):
    deliver = await _play(monkeypatch, filed=False)
    try:
        res, row = await deliver(2, 5)                         # five days later: "Approved."
        assert res["groups"] == 1, res["message"]
        assert "No readable sheets" not in res["message"] and "Nothing to stage" not in res["message"]
        async with SessionLocal() as db:
            fresh = (await db.execute(select(EmailMessage).where(
                EmailMessage.provider_message_id == row.provider_message_id))).scalar_one()
            assert fresh.no_sheets_found_at is None
        item = await _item()
        fe = item.extraction_meta["full_email_extract"]
        assert fe["approval"]["detected"] is True
        assert {s["filename"] for s in fe["sheets"]} == {"week1.pdf"}          # the sheet is still there
        assert len(item.extraction_meta["staged"]["working_days"]) == 5
        assert item.status == PipelineStatus.NEEDS_REVIEW
    finally:
        await _cleanup()


async def test_a_late_approval_on_an_already_filed_month_reopens_it_once_for_the_record(monkeypatch):
    deliver = await _play(monkeypatch, filed=True)
    try:
        await deliver(2, 5)                                    # "Approved." five days later
        item = await _item()
        assert item.extraction_meta["full_email_extract"]["approval"]["detected"] is True
        assert item.record_id == "rec-1"                       # the filed record is kept
        assert item.status == PipelineStatus.NEEDS_REVIEW      # reopened so the approval can be recorded
        assert "approval" in (item.failure_detail or "").lower()

        # the reviewer accepts again; a harmless "Thanks!" afterwards must NOT reopen it
        async with SessionLocal() as db:
            t = (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == KEY))).scalars().one()
            t.status, t.failure_code, t.failure_detail, t.raw_path = PipelineStatus.SUCCESS, None, None, None
            await db.commit()
        await deliver(3, 8)
        assert (await _item()).status == PipelineStatus.SUCCESS
    finally:
        await _cleanup()
