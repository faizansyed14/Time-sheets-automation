"""Copies of ONE timesheet (signed + unsigned, ADR form + company form): read one,
keep them all.

The employee may send the same sheet twice - once plain, once with a manager's
signature - or the client's own form next to the company's template. Reading every
copy wastes calls and hides which copy said what; reading none of them is worse.
So a comparison of the page IMAGES decides whether the day-by-day entries are
identical; only then is one copy (the signed one) read and the others recorded as
"stored with it, not read separately". Any doubt reads everything.
"""
import re

from tests.test_thread_extract import _mail, _p2_sheet


def _p1(src, name="Same Person", emp="E7", signed=False, period="June 2026"):
    return {"source": src, "is_timesheet": True, "kind": "timesheet", "employee_name": name,
            "employee_id": emp, "period_hint": period, "evidence": "1-Jun present",
            "manager_signature": signed, "signature_evidence": "stamp" if signed else "",
            "notes": ""}


async def _run(monkeypatch, *, compare, p1_items, attachments):
    """Scripted model. Returns (labels, sources pass 2 was asked to read, sheets, meta)."""
    from app.core.config import settings
    from app.services.extract_email import thread_prompt, triage_prompt
    from app.services.extract_email.thread_extract import extract_thread_sheets
    from app.services.extraction import vision_client

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    labels: list[str] = []
    asked: list[str] = []

    async def fake_chat(system_prompt, blocks, model, api_key, *, label="", **kw):
        labels.append(label)
        if label.startswith("alternates"):
            if isinstance(compare, BaseException):
                raise compare
            return compare
        if label.startswith("pass1-rescue"):
            return {"thread_summary": "", "items": []}
        if label.startswith("pass1"):
            return {"thread_summary": "The same person sent the sheet twice.", "items": p1_items}
        text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        srcs = re.findall(r"===== ITEM \[(A\d+)\]", text)
        asked.extend(srcs)
        return {"sheets": [_p2_sheet(f"[{k}]", "Same Person", "E7") for k in srcs]}

    monkeypatch.setattr(vision_client, "chat_call", fake_chat)
    monkeypatch.setattr(triage_prompt, "chat_call", fake_chat)
    monkeypatch.setattr(thread_prompt, "chat_call", fake_chat)
    eml = _mail(plain="Timesheet attached, plus the signed one.", attachments=attachments)
    sheets, _a, _c, meta = await extract_thread_sheets([("msg 1", eml)])
    return labels, asked, sheets, meta


_TWO_PDFS = [("plain.pdf", b"%PDF-1.4 plain sheet", "application", "pdf"),
             ("signed.pdf", b"%PDF-1.4 signed sheet", "application", "pdf")]


async def test_signed_and_unsigned_copy_only_the_signed_one_is_read_and_both_recorded(monkeypatch):
    compare = {"items": [{"source": "[A1]", "has_signature_or_stamp": False},
                         {"source": "[A2]", "has_signature_or_stamp": True,
                          "signature_evidence": "manager stamp"}],
               "copy_groups": [{"sources": ["[A1]", "[A2]"], "identical_entries": True,
                                "differences": "A2 has a manager stamp; A1 does not"}]}
    p1 = [_p1("[A1]", signed=False), _p1("[A2]", signed=True)]
    labels, asked, sheets, meta = await _run(
        monkeypatch, compare=compare, p1_items=p1, attachments=_TWO_PDFS)
    assert any(l.startswith("alternates") for l in labels)
    assert asked == ["A2"], asked                       # only the signed copy was sent to be read
    assert [s["name"] for s in sheets] == ["signed.pdf"]
    (rec,) = meta["alternates"]
    assert rec["kept"] == "signed.pdf" and rec["stored_only"] == ["plain.pdf"]
    assert rec["kept_has_signature"] is True


async def test_the_signed_copy_wins_even_when_it_is_listed_first(monkeypatch):
    compare = {"items": [{"source": "[A1]", "has_signature_or_stamp": True},
                         {"source": "[A2]", "has_signature_or_stamp": False}],
               "copy_groups": [{"sources": ["[A2]", "[A1]"], "identical_entries": True,
                                "differences": "A1 is stamped"}]}
    p1 = [_p1("[A1]", signed=True), _p1("[A2]", signed=False)]
    _l, asked, _s, meta = await _run(
        monkeypatch, compare=compare, p1_items=p1,
        attachments=[("signed.pdf", b"%PDF-1.4 s", "application", "pdf"),
                     ("plain.pdf", b"%PDF-1.4 p", "application", "pdf")])
    assert asked == ["A1"] and meta["alternates"][0]["kept"] == "signed.pdf"


async def test_signature_is_judged_from_the_image_not_from_pass1_or_ocr_text(monkeypatch):
    """Pass 1 flagged the WRONG copy as signed (a typed 'Approved by' line in the text
    of the unsigned one); the image comparison says the OTHER copy carries the stamp."""
    compare = {"items": [{"source": "[A1]", "has_signature_or_stamp": False},
                         {"source": "[A2]", "has_signature_or_stamp": True}],
               "copy_groups": [{"sources": ["[A1]", "[A2]"], "identical_entries": True,
                                "differences": "stamp on A2 only"}]}
    p1 = [_p1("[A1]", signed=True), _p1("[A2]", signed=False)]
    _l, asked, _s, meta = await _run(
        monkeypatch, compare=compare, p1_items=p1, attachments=_TWO_PDFS)
    assert asked == ["A2"] and meta["alternates"][0]["kept"] == "signed.pdf"


async def test_adr_form_and_company_form_with_the_same_entries_read_one_store_both(monkeypatch):
    compare = {"items": [{"source": "[A1]", "has_signature_or_stamp": False, "format": "ADR form"},
                         {"source": "[A2]", "has_signature_or_stamp": False, "format": "company template"}],
               "copy_groups": [{"sources": ["[A1]", "[A2]"], "identical_entries": True,
                                "differences": "ADR form versus company template, same entries"}]}
    p1 = [_p1("[A1]"), _p1("[A2]")]
    _l, asked, sheets, meta = await _run(
        monkeypatch, compare=compare, p1_items=p1,
        attachments=[("adr.pdf", b"%PDF-1.4 adr", "application", "pdf"),
                     ("company.pdf", b"%PDF-1.4 company", "application", "pdf")])
    assert len(asked) == 1 and len(sheets) == 1
    (rec,) = meta["alternates"]
    assert sorted([rec["kept"]] + rec["stored_only"]) == ["adr.pdf", "company.pdf"]
    assert "most recent" in rec["how_chosen"]


async def test_copies_with_any_different_entry_are_not_collapsed(monkeypatch):
    compare = {"items": [], "copy_groups": [{"sources": ["[A1]", "[A2]"], "identical_entries": False,
                                              "differences": "A2 marks 12 June as sick"}]}
    p1 = [_p1("[A1]"), _p1("[A2]", signed=True)]
    _l, asked, sheets, meta = await _run(
        monkeypatch, compare=compare, p1_items=p1, attachments=_TWO_PDFS)
    assert sorted(asked) == ["A1", "A2"] and len(sheets) == 2
    assert meta["alternates"] == []


async def test_a_failed_comparison_reads_every_copy_and_says_so(monkeypatch):
    p1 = [_p1("[A1]"), _p1("[A2]", signed=True)]
    _l, asked, sheets, meta = await _run(
        monkeypatch, compare=RuntimeError("boom"), p1_items=p1, attachments=_TWO_PDFS)
    assert sorted(asked) == ["A1", "A2"] and len(sheets) == 2
    assert any("Could not compare" in w for w in meta["warnings"]), meta["warnings"]


async def test_a_reply_naming_an_unknown_item_changes_nothing(monkeypatch):
    compare = {"items": [], "copy_groups": [{"sources": ["[A9]", "[A8]"], "identical_entries": True}]}
    p1 = [_p1("[A1]"), _p1("[A2]")]
    _l, asked, sheets, _m = await _run(
        monkeypatch, compare=compare, p1_items=p1, attachments=_TWO_PDFS)
    assert sorted(asked) == ["A1", "A2"] and len(sheets) == 2


async def test_different_months_or_different_people_are_never_compared(monkeypatch):
    p1 = [_p1("[A1]", period="June 2026"), _p1("[A2]", period="July 2026")]
    labels, _a, sheets, _m = await _run(
        monkeypatch, compare={"items": [], "copy_groups": []}, p1_items=p1, attachments=_TWO_PDFS)
    assert not any(l.startswith("alternates") for l in labels)
    assert len(sheets) == 2

    p1 = [_p1("[A1]", name="Alice Smith", emp="E1"), _p1("[A2]", name="Bob Jones", emp="E2")]
    labels, _a, _s, _m = await _run(
        monkeypatch, compare={"items": [], "copy_groups": []}, p1_items=p1, attachments=_TWO_PDFS)
    assert not any(l.startswith("alternates") for l in labels)


async def test_the_switch_turns_the_whole_step_off(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "dedupe_alternate_copies", False)
    p1 = [_p1("[A1]"), _p1("[A2]", signed=True)]
    labels, asked, _s, _m = await _run(
        monkeypatch, compare={"items": [], "copy_groups": []}, p1_items=p1, attachments=_TWO_PDFS)
    assert not any(l.startswith("alternates") for l in labels)
    assert sorted(asked) == ["A1", "A2"]


def test_period_and_person_prefilter():
    from app.services.extract_email.alternates import maybe_same_person, period_of

    assert period_of("June 2026") == (2026, 6) and period_of("1-7 Jun 2026") == (2026, 6)
    assert period_of("06/2026") == (2026, 6) and period_of("2026-06") == (2026, 6)
    assert period_of("the first week") is None
    assert maybe_same_person({"employee_id": "E-7"}, {"employee_id": "e7"})
    assert maybe_same_person({"employee_name": "Ahmed Al Rashid"}, {"employee_name": "Rashid Ahmed Al"})
    assert not maybe_same_person({"employee_name": "Alice Smith"}, {"employee_name": "Bob Jones"})


async def test_one_item_stores_the_alternate_and_its_note_without_blocking_anything():
    """The kept sheet's group gets the 'also received' note and the stored-only file
    names travel with the item (so a multi-employee email still files both copies)."""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.pipeline_file import PipelineFile
    from app.services.extract_email.grouping import group_sheets
    from app.services.extract_email.staging import stage_groups
    from app.services.pipeline.ingestion import isolate_employee_attachments

    sheet = {"kind": "timesheet", "name": "signed.pdf", "employee_name": "Alt Person",
             "employee_id": "E-ALT-1", "month": 6, "year": 2026, "period_type": "full_month",
             "days_covered": 3, "missing_days": [], "working_days": ["2026-06-01", "2026-06-02", "2026-06-03"],
             "weekend_days": [], "uncertain_days": [], "annual": [], "remote": [], "sick": [],
             "maternity": [], "unpaid": [], "absent": [], "public_holiday": [], "other": [],
             "notes": "", "manager_signature": True, "approval_evidence": "stamp",
             "approval_named_only": False}
    alt = {"kept": "signed.pdf", "kept_key": "A2", "stored_only": ["plain.pdf"],
           "kept_has_signature": True, "differences": "stamp on signed.pdf only",
           "how_chosen": "the copy that carries a signature/stamp"}
    meta = {"method": "thread-two-pass", "model": "m", "errors": [], "warnings": [],
            "incomplete": False, "alternates": [alt]}
    key = "alt-thread-1"
    try:
        async with SessionLocal() as db:
            groups = await group_sheets(db, None, [sheet])
            (item,) = await stage_groups(
                db, source_kind="email", source_id="alt-msg-1", raw_bytes=b"raw", raw_name="x.eml",
                content_type="message/rfc822", groups=groups,
                approval={"detected": True, "where": "sheet", "detail": "x"},
                run_meta=meta, thread_key=key)
            fe = item.extraction_meta["full_email_extract"]
            assert fe["alternates"] == [alt]
            flags = item.extraction_meta["staged"]["flags"]
            assert any("plain.pdf" in f and "stored with this item" in f for f in flags), flags
            # a multi-employee email: the stored-only copy is filed with this employee too
            fe["group_count"] = 2
            fe["sheets"] = [{"filename": "signed.pdf"}]
    finally:
        async with SessionLocal() as db:
            for r in (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == key))).scalars():
                await db.delete(r)
            await db.commit()

    from email.message import EmailMessage as Mime
    m = Mime()
    m["Subject"] = "x"
    m.set_content("hi")
    m.add_attachment(b"%PDF-1.4 signed", maintype="application", subtype="pdf", filename="signed.pdf")
    m.add_attachment(b"%PDF-1.4 plain", maintype="application", subtype="pdf", filename="plain.pdf")
    m.add_attachment(b"%PDF-1.4 other", maintype="application", subtype="pdf", filename="someone-else.pdf")
    t = PipelineFile(source_kind="email", extraction_meta={"full_email_extract": {
        "group_count": 2, "sheets": [{"filename": "signed.pdf"}], "alternates": [alt]}})
    got = isolate_employee_attachments(t, m.as_bytes())
    assert got is not None
    assert sorted(n for n, _c, _d in got) == ["plain.pdf", "signed.pdf"]
