"""A conversation that keeps growing: reply after reply, nothing is lost or doubled.

Eight messages arrive one at a time in ONE thread - two weekly sheets, an "Approved"
reply, a third week, a "thanks", a fourth week, a second employee's sheet and an "ok".
After every message the real extraction runs (scripted model, real database, real
locks). Whatever the number of replies:

  * one review item per employee + month (never a second copy, never a deleted one),
  * every sheet ever read is still on it - earlier weeks are not forgotten when a
    later message is read on its own,
  * the approval found in a reply is not lost by later replies,
  * the stored copy of the conversation keeps EVERY message and attachment, not just
    the newest few that the model was shown this time.
"""
import datetime as dt
import email
import email.policy
import re
from email.message import EmailMessage as Mime

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.models.pipeline_file import PipelineFile

KEY = "conv-growth-e2e"
ASKED: list[str] = []          # attachment names pass 2 was asked to read (reset by each test)
BASE = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)   # message i arrives at BASE + i hours

# message number -> (attachment filename or None, body)
THREAD = {
    1: ("week1.pdf", "Week 1 attached."),
    2: ("week2.pdf", "Week 2 attached."),
    3: (None, "Approved."),
    4: ("week3.pdf", "Week 3 attached."),
    5: (None, "Thanks!"),
    6: ("week4.pdf", "Week 4 attached."),
    7: ("other.pdf", "Another person's sheet."),
    8: (None, "ok"),
}
WEEK_DAYS = {"week1.pdf": 1, "week2.pdf": 8, "week3.pdf": 15, "week4.pdf": 22, "other.pdf": 1}


def _eml(i: int) -> bytes:
    fname, body = THREAD[i]
    m = Mime()
    m["Subject"] = f"Re: June timesheets ({i})"
    m["From"] = "employee@alpha.ae"
    m.set_content(body)
    if fname:
        m.add_attachment(f"%PDF-1.4 {fname}".encode(), maintype="application", subtype="pdf", filename=fname)
    return m.as_bytes()


async def _cleanup():
    async with SessionLocal() as db:
        for t in (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == KEY))).scalars():
            await db.delete(t)
        for r in (await db.execute(select(EmailMessage).where(EmailMessage.conversation_id == KEY))).scalars():
            await db.delete(r)
        await db.commit()


def _scripted_model(monkeypatch):
    from app.core.config import settings
    from app.services.extract_email import thread_prompt, triage_prompt
    from app.services.extraction import vision_client

    monkeypatch.setattr(settings, "pass1_batch_size", 50)
    calls: list[str] = []

    def items_in(blocks):
        text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        return re.findall(r"^\s+\[(A\d+)\] (.+?) · from message (\d+)", text, re.M)

    async def fake_chat(system_prompt, blocks, model, api_key, *, label="", **kw):
        calls.append(label)
        if label.startswith("alternates"):
            return {"items": [], "copy_groups": []}
        if label.startswith("pass1"):
            out = []
            for key, name, msg_no in items_in(blocks):
                name = name.strip()
                if name.endswith(".pdf"):
                    who = "Other Person" if name == "other.pdf" else "Growth Person"
                    out.append({"source": f"[{key}]", "is_timesheet": True, "kind": "timesheet",
                                "employee_name": who, "employee_id": None, "period_hint": "June 2026",
                                "evidence": "1-Jun present", "manager_signature": False,
                                "signature_evidence": "", "notes": ""})
                elif name.startswith("email body") and THREAD[int(msg_no)][1] == "Approved.":
                    out.append({"source": f"[{key}]", "kind": "approval", "manager_signature": True,
                                "signature_evidence": "manager replied Approved"})
                else:
                    out.append({"source": f"[{key}]", "kind": "other"})
            return {"thread_summary": "weekly sheets for June", "items": out}
        text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        sheets = []
        for key, name in re.findall(r"===== ITEM \[(A\d+)\] (.+?) =====", text):
            start = WEEK_DAYS.get(name.strip())
            if start is None:
                continue
            ASKED.append(name.strip())
            days = [f"2026-06-{d:02d}" for d in range(start, start + 5)]
            who = "Other Person" if name.strip() == "other.pdf" else "Growth Person"
            sheets.append({"source": f"[{key}]", "employee_name": who, "employee_id": None,
                           "month": 6, "year": 2026, "days_covered": 5, "period_type": "week",
                           "missing_days": [], "working_days": days, "weekend_days": [],
                           "uncertain_days": [], "annual": [], "remote": [], "sick": [],
                           "maternity": [], "unpaid": [], "absent": [], "public_holiday": [], "notes": ""})
        return {"sheets": sheets}

    for mod in (vision_client, triage_prompt, thread_prompt):
        monkeypatch.setattr(mod, "chat_call", fake_chat)
    return calls


async def _rows():
    async with SessionLocal() as db:
        return (await db.execute(select(PipelineFile).where(
            PipelineFile.thread_key == KEY,
            PipelineFile.attachment_id.like("__email_extract__%")))).scalars().all()


def _raw_attachment_names(raw: bytes) -> set[str]:
    names: set[str] = set()

    def walk(msg):
        for part in msg.walk():
            fn = part.get_filename()
            if fn:
                names.add(fn)
            if part.get_content_type() == "message/rfc822":
                for sub in part.iter_parts() if hasattr(part, "iter_parts") else []:
                    walk(sub)
    walk(email.message_from_bytes(raw, policy=email.policy.default))
    return names


async def test_a_thread_that_keeps_growing_loses_nothing_and_duplicates_nothing(monkeypatch):
    from app.services.email_provider.base import ProviderMessage
    from app.services.extract_email.email import extract_full_email
    from app.services.pipeline import raw_store
    from tests.test_incremental_extraction import _FakeProvider

    from app.services.extract_email import email as email_mod

    _scripted_model(monkeypatch)
    await _cleanup()

    # A realistic clock: run i happens 30 minutes AFTER message i arrived and before
    # message i+1, so a later run only has the new message(s) to read (incremental
    # window) - the path real replies take.
    clock = {"now": BASE}

    class _Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(email_mod, "datetime", _Clock)
    provider_msgs: list = []
    raw_by_id: dict[str, bytes] = {}
    provider = _FakeProvider(provider_msgs, raw_by_id)
    monkeypatch.setattr("app.services.email_provider.get_email_provider", lambda: provider)

    try:
        for i in range(1, len(THREAD) + 1):
            mid = f"{KEY}-{i}"
            received = BASE + dt.timedelta(hours=i)
            provider_msgs.append(ProviderMessage(
                message_id=mid, sender_name="E", sender_email="employee@alpha.ae",
                subject=f"Re: June timesheets ({i})", received_at=received,
                body_text=THREAD[i][1], conversation_id=KEY))
            raw_by_id[mid] = _eml(i)
            clock["now"] = received + dt.timedelta(minutes=30)
            async with SessionLocal() as db:
                db.add(EmailMessage(
                    provider_message_id=mid, conversation_id=KEY, sender_name="E",
                    sender_email="employee@alpha.ae", subject=f"Re: June timesheets ({i})",
                    received_at=received, body_text=THREAD[i][1], attachments=[]))
                await db.commit()
                row = (await db.execute(select(EmailMessage).where(
                    EmailMessage.provider_message_id == mid))).scalar_one()
                res = await extract_full_email(db, row)
            assert not res.get("incomplete"), (i, res.get("errors"))

            rows = await _rows()
            who = {r.employee_name for r in rows}
            expected_people = {"Growth Person"} | ({"Other Person"} if i >= 7 else set())
            assert who == expected_people, (i, who)
            assert len(rows) == len(expected_people), (i, [r.attachment_id for r in rows])

            growth = next(r for r in rows if r.employee_name == "Growth Person")
            fe = growth.extraction_meta["full_email_extract"]
            sheet_names = {s["filename"] for s in fe["sheets"]}
            weeks_so_far = {f for n, (f, _b) in THREAD.items() if n <= i and f and f.startswith("week")}
            assert sheet_names == weeks_so_far, (i, sheet_names, weeks_so_far)
            staged = growth.extraction_meta["staged"]
            assert staged["month"] == 6 and staged["year"] == 2026
            # five working days per week sheet, none lost, none counted twice
            assert len(staged["working_days"]) == 5 * len(weeks_so_far), (i, staged["working_days"])

            if i >= 3:
                assert fe["approval"]["detected"] is True, (i, fe["approval"])

        # the stored copy of the conversation still has EVERY message and attachment
        for r in await _rows():
            raw = raw_store.read_raw(r.raw_path)
            assert raw, r.employee_name
            names = _raw_attachment_names(raw)
            every_attachment = {f for f, _b in THREAD.values() if f}
            assert every_attachment <= names, (r.employee_name, names)
            assert raw.count(b"June timesheets") >= len(THREAD) or all(
                f"({n})".encode() in raw for n in THREAD), r.employee_name
    finally:
        await _cleanup()


async def test_if_the_whole_conversation_cannot_be_rebuilt_the_stored_copy_is_kept_not_shrunk(monkeypatch):
    """The mailbox cannot return an old message right now: the new reply is still read
    from its own window, but the evidence already stored is NOT replaced by a shorter
    bundle, and the run says so."""
    from app.services.email_provider.base import ProviderMessage
    from app.services.extract_email import email as email_mod
    from app.services.extract_email.email import extract_full_email
    from app.services.pipeline import raw_store
    from tests.test_incremental_extraction import _FakeProvider

    _scripted_model(monkeypatch)
    monkeypatch.setattr("app.services.extract_email.thread_collect._FETCH_RETRY_DELAY", 0)
    await _cleanup()
    clock = {"now": BASE}

    class _Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(email_mod, "datetime", _Clock)
    provider_msgs: list = []
    raw_by_id: dict[str, bytes] = {}
    monkeypatch.setattr("app.services.email_provider.get_email_provider",
                        lambda: _FakeProvider(provider_msgs, raw_by_id))
    try:
        res = None
        for i in range(1, 5):
            mid = f"{KEY}-{i}"
            received = BASE + dt.timedelta(hours=i)
            provider_msgs.append(ProviderMessage(
                message_id=mid, sender_name="E", sender_email="employee@alpha.ae",
                subject=f"Re: June timesheets ({i})", received_at=received,
                body_text=THREAD[i][1], conversation_id=KEY))
            raw_by_id[mid] = _eml(i)
            clock["now"] = received + dt.timedelta(minutes=30)
            if i == 4:
                raw_by_id.pop(f"{KEY}-1")           # an old message can no longer be downloaded
            async with SessionLocal() as db:
                db.add(EmailMessage(
                    provider_message_id=mid, conversation_id=KEY, sender_name="E",
                    sender_email="employee@alpha.ae", subject=f"Re: June timesheets ({i})",
                    received_at=received, body_text=THREAD[i][1], attachments=[]))
                await db.commit()
                row = (await db.execute(select(EmailMessage).where(
                    EmailMessage.provider_message_id == mid))).scalar_one()
                res = await extract_full_email(db, row)
        assert "could not be refreshed" in res["message"], res["message"]
        (item,) = await _rows()
        names = _raw_attachment_names(raw_store.read_raw(item.raw_path))
        assert {"week1.pdf", "week2.pdf"} <= names, names   # the earlier, fuller copy is still there
        # ...and the new message's own sheet WAS read this run
        assert "week3.pdf" in {s["filename"] for s in item.extraction_meta["full_email_extract"]["sheets"]}
    finally:
        await _cleanup()


async def test_extracting_only_the_ticked_messages_reads_only_those_and_loses_nothing(monkeypatch):
    """The reviewer ticks message 2 only: the model reads ONLY message 2 (fresh), every
    other sheet stays on the item, the stored thread is still whole, no item is removed,
    and the thread's "fully read up to here" mark does NOT move - so a reply that arrives
    afterwards (or an unread one) is still found by the normal scan."""
    from app.services.email_provider.base import ProviderMessage
    from app.services.extract_email import email as email_mod
    from app.services.extract_email import sheet_cache
    from app.services.extract_email.email import extract_full_email
    from app.services.pipeline import raw_store
    from tests.test_incremental_extraction import _FakeProvider

    _scripted_model(monkeypatch)
    await _cleanup()
    clock = {"now": BASE}

    class _Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(email_mod, "datetime", _Clock)
    provider_msgs: list = []
    raw_by_id: dict[str, bytes] = {}
    monkeypatch.setattr("app.services.email_provider.get_email_provider",
                        lambda: _FakeProvider(provider_msgs, raw_by_id))

    async def add_message(i):
        mid = f"{KEY}-{i}"
        received = BASE + dt.timedelta(hours=i)
        provider_msgs.append(ProviderMessage(
            message_id=mid, sender_name="E", sender_email="employee@alpha.ae",
            subject=f"Re: June timesheets ({i})", received_at=received,
            body_text=THREAD[i][1], conversation_id=KEY))
        raw_by_id[mid] = _eml(i)
        clock["now"] = received + dt.timedelta(minutes=30)
        async with SessionLocal() as db:
            db.add(EmailMessage(
                provider_message_id=mid, conversation_id=KEY, sender_name="E",
                sender_email="employee@alpha.ae", subject=f"Re: June timesheets ({i})",
                received_at=received, body_text=THREAD[i][1], attachments=[]))
            await db.commit()

    async def run(i, **kw):
        async with SessionLocal() as db:
            row = (await db.execute(select(EmailMessage).where(
                EmailMessage.provider_message_id == f"{KEY}-{i}"))).scalar_one()
            return await extract_full_email(db, row, **kw)

    try:
        for i in (1, 2, 3, 4):
            await add_message(i)
            await run(i)                               # normal incremental runs: weeks 1, 2, (approval), 3
        async with SessionLocal() as db:
            mark_before = await sheet_cache.last_extraction_at(db, KEY)

        ASKED.clear()
        res = await run(4, only_message_ids=[f"{KEY}-2"])
        assert not res.get("incomplete"), res.get("errors")
        assert ASKED == ["week2.pdf"], ASKED           # only the ticked message was read

        (item,) = await _rows()
        fe = item.extraction_meta["full_email_extract"]
        assert {s["filename"] for s in fe["sheets"]} == {"week1.pdf", "week2.pdf", "week3.pdf"}
        assert len(item.extraction_meta["staged"]["working_days"]) == 15      # nothing lost, nothing doubled
        names = _raw_attachment_names(raw_store.read_raw(item.raw_path))
        assert {"week1.pdf", "week2.pdf", "week3.pdf"} <= names                # stored thread still whole
        assert "selected message" in res["message"]

        async with SessionLocal() as db:
            assert await sheet_cache.last_extraction_at(db, KEY) == mark_before   # mark did not move

        # a message that arrives afterwards is still picked up by the normal path
        await add_message(6)
        ASKED.clear()
        await run(6)
        assert ASKED == ["week4.pdf"], ASKED
    finally:
        await _cleanup()


async def test_selecting_every_message_is_just_the_full_re_read_the_old_button_did(monkeypatch):
    """"Select all" is sent as force_full: every attachment is read again."""
    from app.services.email_provider.base import ProviderMessage
    from app.services.extract_email import email as email_mod
    from app.services.extract_email.email import extract_full_email
    from tests.test_incremental_extraction import _FakeProvider

    _scripted_model(monkeypatch)
    await _cleanup()
    clock = {"now": BASE}

    class _Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    monkeypatch.setattr(email_mod, "datetime", _Clock)
    provider_msgs: list = []
    raw_by_id: dict[str, bytes] = {}
    monkeypatch.setattr("app.services.email_provider.get_email_provider",
                        lambda: _FakeProvider(provider_msgs, raw_by_id))
    try:
        for i in (1, 2):
            mid = f"{KEY}-{i}"
            received = BASE + dt.timedelta(hours=i)
            provider_msgs.append(ProviderMessage(
                message_id=mid, sender_name="E", sender_email="employee@alpha.ae",
                subject=f"Re: June timesheets ({i})", received_at=received,
                body_text=THREAD[i][1], conversation_id=KEY))
            raw_by_id[mid] = _eml(i)
            clock["now"] = received + dt.timedelta(minutes=30)
            async with SessionLocal() as db:
                db.add(EmailMessage(
                    provider_message_id=mid, conversation_id=KEY, sender_name="E",
                    sender_email="employee@alpha.ae", subject=f"Re: June timesheets ({i})",
                    received_at=received, body_text=THREAD[i][1], attachments=[]))
                await db.commit()
                row = (await db.execute(select(EmailMessage).where(
                    EmailMessage.provider_message_id == mid))).scalar_one()
                await extract_full_email(db, row)
        ASKED.clear()
        async with SessionLocal() as db:
            row = (await db.execute(select(EmailMessage).where(
                EmailMessage.provider_message_id == f"{KEY}-2"))).scalar_one()
            await extract_full_email(db, row, force_full=True)
        assert sorted(ASKED) == ["week1.pdf", "week2.pdf"], ASKED
        (item,) = await _rows()                         # re-read updates the same item, no second copy
        assert len(item.extraction_meta["staged"]["working_days"]) == 10
    finally:
        await _cleanup()
