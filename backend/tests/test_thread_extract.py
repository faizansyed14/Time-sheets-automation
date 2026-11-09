"""The Item/Message/Thread collector (thread_extract.collect_thread) and the
two-pass orchestration built on top of it.

Ported design (docs/timesheet_strong_prompt.ipynb): every attachment AND every
message body becomes its own [A#] item; a stable key is unambiguous by
construction, unlike matching on filename alone.
"""
from email.message import EmailMessage as MimeMessage

from app.services.extract_email.thread_extract import (
    Item,
    Thread,
    _content_digest,
    _dedupe_sheet_items,
    collect_thread,
    require_vision_configured,
    resolve_source,
)


_PNG_MAGIC = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10])


def _png_bytes(*, big: bool) -> bytes:
    """A real, decodable PNG — the collector actually opens every image with
    PIL (to resize/re-encode it), so a fake byte string with a valid magic
    number but garbage pixel data fails to decode and vanishes silently.
    Random noise (not a solid colour) so PNG compression can't shrink a
    "big" image back under the size floor."""
    import io as _io
    import random

    from PIL import Image

    side = 400 if big else 20
    rng = random.Random(0)
    img = Image.new("RGB", (side, side))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                 for _ in range(side * side)])
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mail(*, subject="TIMESHEET June 2026", plain="", html=None, attachments=()):
    m = MimeMessage()
    m["Subject"] = subject
    m["From"] = "employee@alpha.ae"
    m["To"] = "timesheet@alpha.ae"
    m.set_content(plain or "See attached.")
    if html:
        m.add_alternative(html, subtype="html")
    for fn, payload, maintype, subtype in attachments:
        m.add_attachment(payload, maintype=maintype, subtype=subtype, filename=fn)
    return m.as_bytes()


# --------------------------------------------------------------------------
# What gets collected, and how it's labelled
# --------------------------------------------------------------------------

def test_every_attachment_and_body_gets_a_stable_a_key():
    eml = _mail(plain="Here is my sheet.", attachments=[
        ("sheet.pdf", b"%PDF-1.4 fake sheet", "application", "pdf"),
    ])
    th = collect_thread([("msg 1", eml)])
    keys = [it.key for it in th.items]
    assert keys == sorted(keys, key=lambda k: int(k[1:])), "keys should be assigned in order"
    assert len(set(keys)) == len(keys), "every item key must be unique"
    names = [it.name for it in th.items]
    assert "sheet.pdf" in names
    assert any(n.startswith("email body") for n in names)


def test_resolve_source_matches_by_bracketed_key():
    eml = _mail(attachments=[("sheet.pdf", b"%PDF-1.4 x", "application", "pdf")])
    th = collect_thread([("msg 1", eml)])
    sheet_item = next(it for it in th.items if it.name == "sheet.pdf")
    assert resolve_source(f"[{sheet_item.key}] sheet.pdf", th.items) is sheet_item
    assert resolve_source(sheet_item.key, th.items) is sheet_item
    assert resolve_source("sheet.pdf", th.items) is sheet_item
    assert resolve_source("nothing-like-this", th.items) is None


def _inline_mail(payload: bytes, *, cid: str = "sig1") -> bytes:
    """A mail whose HTML body embeds `payload` as a cid image (signature-style)."""
    m = MimeMessage()
    m["Subject"] = "TIMESHEET June 2026"
    m["From"] = "employee@alpha.ae"
    m["To"] = "timesheet@alpha.ae"
    m.set_content("See below.")
    m.add_alternative(f'<html><body><p>hi</p><img src="cid:{cid}"></body></html>', subtype="html")
    m.get_payload()[1].add_related(payload, maintype="image", subtype="png", cid=f"<{cid}>")
    return m.as_bytes()


def test_tracking_pixel_is_dropped_but_a_small_named_screenshot_is_kept():
    """Only a sub-1KB image (tracking pixel / bullet) is dropped on size alone.
    A small NAMED attachment - a weekly-timesheet screenshot is often well under
    30KB - must reach the model: dropping it silently lost whole weeks."""
    eml = _mail(attachments=[
        ("pixel.png", _PNG_MAGIC + b"0" * 100, "image", "png"),
        ("week1.png", _png_bytes(big=False), "image", "png"),
    ])
    th = collect_thread([("msg 1", eml)])
    assert not any(it.name == "pixel.png" for it in th.items)
    assert any(d["name"] == "pixel.png" and d["filter"] == "size" for d in th.dropped)
    assert any(it.name == "week1.png" for it in th.items), "small named screenshot must be kept"


def test_small_inline_logo_is_dropped_only_when_ocr_finds_no_text(monkeypatch):
    """A small EMBEDDED image is a logo/stamp suspect: dropped when OCR ran and
    found nothing, kept when OCR finds real text (a pasted table screenshot)."""
    from app.core.config import settings
    from app.services.extraction import ocr as ocr_mod

    # The 30 KB size rule is covered in test_small_images_and_weak_names; switch it off
    # here so this test isolates the OCR rule that applies to LARGER embedded pictures.
    monkeypatch.setattr(settings, "min_image_bytes", 0)
    eml = _inline_mail(_png_bytes(big=False))
    monkeypatch.setattr(ocr_mod, "ocr_status", lambda: "ready")

    monkeypatch.setattr(ocr_mod, "ocr_text", lambda *a, **k: "")
    th = collect_thread([("msg 1", eml)])
    assert not any(it.mime.startswith("image/") for it in th.items)
    assert any(d.get("filter") == "ocr" for d in th.dropped)

    monkeypatch.setattr(ocr_mod, "ocr_text", lambda *a, **k: "Mon 1 June 8h  Tue 2 June 8h  Wed 3 June 8h")
    th = collect_thread([("msg 1", eml)])
    assert any(it.mime.startswith("image/") for it in th.items), "inline image with text must be kept"


def test_undecodable_image_is_reported_not_silently_lost():
    eml = _mail(attachments=[("broken.png", _PNG_MAGIC + b"x" * 5000, "image", "png")])
    th = collect_thread([("msg 1", eml)])
    assert not any(it.name == "broken.png" for it in th.items)
    assert any("broken.png" in u for u in th.unreadable)
    assert any(d["name"] == "broken.png" and d["filter"] == "unreadable" for d in th.dropped)


def test_deeply_nested_forward_is_reported_not_silently_skipped(monkeypatch):
    from app.core.config import settings

    inner = _mail(subject="inner", attachments=[("s.pdf", b"%PDF-1.4 x", "application", "pdf")])
    mid = _mail(subject="mid", attachments=[("inner.eml", inner, "message", "rfc822")])
    # Attach as a FILE so depth is counted (a MIME-nested part is unwrapped for free).
    outer = MimeMessage()
    outer["Subject"] = "outer"
    outer.set_content("fwd")
    outer.add_attachment(mid, maintype="application", subtype="octet-stream", filename="mid.eml")
    monkeypatch.setattr(settings, "max_eml_depth", 1)
    th = collect_thread([("msg 1", outer.as_bytes())])
    assert any("mid.eml" in u or "nested" in u for u in th.unreadable), th.unreadable


def test_real_sized_image_without_ocr_available_is_kept():
    """When OCR can't actually run (no engine binary), the noise filter must
    not treat that as "no text -> drop it" — every real picture attachment is
    kept, exactly like the notebook does when its OCR check is unavailable."""
    from app.services.extraction import ocr as ocr_mod

    eml = _mail(attachments=[("screenshot.png", _png_bytes(big=True), "image", "png")])
    assert len(eml) > 0

    orig_status = ocr_mod.ocr_status
    try:
        ocr_mod.ocr_status = lambda: "not_installed"
        th = collect_thread([("msg 1", eml)])
    finally:
        ocr_mod.ocr_status = orig_status
    assert any(it.name == "screenshot.png" for it in th.items)


def test_html_table_body_survives_as_text_never_as_an_image():
    """Conversation text is always plain text — a pasted grid in the body
    becomes a text item, never a rendered picture (unlike a real attachment)."""
    html = ("<html><body><p>Hi</p><table>"
            "<tr><td>1-June-26</td><td>Sick Leave</td></tr>"
            "<tr><td>2-June-26</td><td>Annual Leave</td></tr>"
            "</table></body></html>")
    th = collect_thread([("msg 1", _mail(plain="Hi team", html=html))])
    body_item = next(it for it in th.items if it.name.startswith("email body"))
    assert "1-June-26" in body_item.text
    assert "Sick Leave" in body_item.text
    assert body_item.images == []


def test_every_message_body_is_included_oldest_first():
    a = _mail(subject="TIMESHEET June", plain="Here is my sheet.")
    b = _mail(subject="RE: TIMESHEET June", plain="Approved by me.")
    th = collect_thread([("msg 1", a), ("msg 2", b)])
    assert [m.body for m in th.messages] == ["Here is my sheet.", "Approved by me."]


def test_csv_and_txt_attachments_are_native_text():
    eml = _mail(attachments=[
        ("data.csv", b"date,status\n2026-06-01,present\n", "text", "csv"),
        ("notes.txt", b"free-form notes about the month", "text", "plain"),
    ])
    th = collect_thread([("msg 1", eml)])
    csv_item = next(it for it in th.items if it.name == "data.csv")
    txt_item = next(it for it in th.items if it.name == "notes.txt")
    assert "2026-06-01" in csv_item.text
    assert csv_item.send_mode == "native"
    assert "free-form notes" in txt_item.text
    assert txt_item.send_mode == "native"


def _zip_bytes(*, with_xl_folder: bool = False) -> bytes:
    """A real, valid zip — detect_file_type actually opens it with
    zipfile.ZipFile, so a fake byte string with just a "PK" prefix wouldn't
    exercise the real internal-path check."""
    import io as _io
    import zipfile as _zipfile

    buf = _io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as zf:
        if with_xl_folder:
            zf.writestr("xl/worksheets/sheet1.xml", "<x/>")
        else:
            zf.writestr("readme.txt", "just some files")
    return buf.getvalue()


def test_zip_attachment_is_never_extracted():
    eml = _mail(attachments=[("archive.zip", _zip_bytes(), "application", "zip")])
    th = collect_thread([("msg 1", eml)])
    assert not any(it.name == "archive.zip" for it in th.items)
    dropped = next(d for d in th.dropped if d["name"] == "archive.zip")
    assert dropped["filter"] == "zip"


def test_zip_with_xlsx_style_internal_paths_is_still_never_opened_as_a_spreadsheet():
    """A zip whose contents happen to start with xl/ — the same internal
    layout a real .xlsx has — must still be skipped as a zip, never handed
    to openpyxl. The .zip extension/content-type check runs BEFORE
    detect_file_type's own xl//word/ internal-path heuristic, specifically
    so this can't be misdetected as a real Office document."""
    eml = _mail(attachments=[("weird.zip", _zip_bytes(with_xl_folder=True), "application", "zip")])
    th = collect_thread([("msg 1", eml)])
    assert not any(it.name == "weird.zip" for it in th.items)
    dropped = next(d for d in th.dropped if d["name"] == "weird.zip")
    assert dropped["filter"] == "zip"


# --------------------------------------------------------------------------
# Emails/messages inside emails (forwarded)
# --------------------------------------------------------------------------

def _eml_attached(inner: bytes, *, filename="forwarded.eml", as_message=False) -> bytes:
    m = MimeMessage()
    m["Subject"] = "FW: timesheet"
    m["From"] = "fwd@alpha.ae"
    m.set_content("Forwarding, see attached.")
    if as_message:
        m.add_attachment(inner, maintype="message", subtype="rfc822", filename=filename)
    else:
        # How Outlook actually attaches a saved .eml — as an opaque file.
        m.add_attachment(inner, maintype="application", subtype="octet-stream", filename=filename)
    return m.as_bytes()


def test_eml_attached_as_a_file_is_opened():
    """An .eml attached as application/octet-stream is just bytes — MIME
    walking never sees inside it unless unwrapped explicitly."""
    inner = _mail(subject="Inner", plain="inner body text",
                  attachments=[("hidden.pdf", b"%PDF-1.4 hidden", "application", "pdf")])
    th = collect_thread([("msg 1", _eml_attached(inner))])
    assert any(it.name == "hidden.pdf" for it in th.items)
    assert any("inner body text" in m.body for m in th.messages)


def test_eml_nested_several_levels_deep_is_opened():
    """A forward of a forward of a forward still carries a real sheet."""
    deepest = _mail(subject="Deepest", plain="deep",
                    attachments=[("deep.pdf", b"%PDF-1.4 deep", "application", "pdf")])
    mid = _eml_attached(deepest, filename="level3.eml")
    top = _eml_attached(mid, filename="level2.eml")
    th = collect_thread([("msg 1", top)])
    assert any(it.name == "deep.pdf" for it in th.items)


def test_eml_recursion_is_depth_limited():
    """A mail loop must not recurse forever."""
    import app.services.extract_email.thread_extract as te

    payload = _mail(subject="base", plain="base")
    for _ in range(te.MAX_EML_DEPTH + 3):
        payload = _eml_attached(payload)
    collect_thread([("msg 1", payload)])   # must simply return, not recurse forever


def test_msg_to_eml_bytes_uses_asEmailMessage_not_export(monkeypatch):
    """Real bug, found live: msg_to_eml_bytes used to call bare `msg.export()`.
    export()/exportBytes() re-save the SAME .msg (OLE) format — never RFC822
    — and export() also requires a destination path argument and returns
    None, so calling it with none raised TypeError on EVERY .msg,
    unconditionally. The broad except around it swallowed that silently, so
    .msg conversion had never actually worked at all — every .msg fell back
    to being wrapped as an opaque, unreadable blob. asEmailMessage() is the
    correct call: it returns a real stdlib EmailMessage, which .as_bytes()
    turns into genuine RFC822 bytes. A real .msg (OLE compound binary) can't
    be constructed in a unit test, so extract_msg.Message itself is faked
    here — this pins the WIRING, not the library's own parsing."""
    import sys
    import app.services.extract_email.thread_extract as te

    real_eml = MimeMessage()
    real_eml["Subject"] = "From a real .msg"
    real_eml.set_content("Body recovered from the .msg file.")

    calls = []

    class _FakeMsg:
        def asEmailMessage(self):
            calls.append("asEmailMessage")
            return real_eml

        def export(self, *a, **kw):
            raise AssertionError("export() must never be called — wrong format, wrong signature")

        def exportBytes(self, *a, **kw):
            raise AssertionError("exportBytes() must never be called — wrong format")

        def close(self):
            calls.append("close")

    class _FakeExtractMsgModule:
        @staticmethod
        def Message(path):
            return _FakeMsg()

    monkeypatch.setitem(sys.modules, "extract_msg", _FakeExtractMsgModule())

    result = te.msg_to_eml_bytes(b"fake ole compound bytes")
    assert calls == ["asEmailMessage", "close"]
    assert result is not None

    import email as _email_mod
    import email.policy as _policy_mod
    parsed = _email_mod.message_from_bytes(result, policy=_policy_mod.compat32)
    assert parsed["Subject"] == "From a real .msg"


# --------------------------------------------------------------------------
# require_vision_configured
# --------------------------------------------------------------------------

def test_require_vision_configured_passes_with_a_key():
    # conftest sets a fake OPENAI_API_KEY so the thread pipeline can run in
    # tests without a real network call (vision_client.chat_call is mocked
    # per-test where needed).
    api_key, model = require_vision_configured()
    assert api_key
    assert model


def test_require_vision_configured_raises_without_a_key(monkeypatch):
    from app.core.config import settings as s
    monkeypatch.setattr(s, "openai_api_key", "")
    import pytest
    with pytest.raises(RuntimeError):
        require_vision_configured()


# --------------------------------------------------------------------------
# Same-sheet dedupe (re-attached / regenerated with manager sign)
# --------------------------------------------------------------------------

_SHEET_CORE = (
    "Employee: Ayaz Kardame\n"
    "Period: June 2026\n"
    "Project: Digital Platforms\n"
    "Days: Mon-Fri present\n"
    "Total hours: 176\n"
)


def test_exact_same_attachment_bytes_keep_one_sheet():
    payload = b"%PDF-1.4 identical-timesheet-body"
    digest = _content_digest(payload)
    th = Thread(
        subject="June",
        items=[
            Item(key="A1", name="June.pdf", mime="application/pdf",
                 msg_index=0, text=_SHEET_CORE, size=len(payload), digest=digest),
            Item(key="A2", name="June-1.pdf", mime="application/pdf",
                 msg_index=2, text=_SHEET_CORE, size=len(payload), digest=digest),
        ],
    )
    _dedupe_sheet_items(th)
    assert [it.key for it in th.items] == ["A2"]
    assert len(th.dropped) == 1
    assert th.dropped[0]["filter"] == "dup"
    assert th.dropped[0]["kept_key"] == "A2"
    assert th.dropped[0]["name"] == "June.pdf"


def test_near_same_ocr_no_longer_guesses_which_approval_variant_to_drop():
    """Real production bug this pins down: an unsigned copy + the same sheet
    regenerated with Approved By/manager sign used to be collapsed to
    "whichever scores higher on approval keywords" — and that heuristic
    picked the WRONG one for a real employee (blank template fields like
    "Approved by: ______" score too). Both must now survive untouched;
    picking which is authoritative is grouping.py's overlap-flag + a human's
    job, never a keyword count's."""
    unsigned = _SHEET_CORE + "Status: Submitted\n"
    signed = (
        _SHEET_CORE
        + "Approved By: Ahmed Shukri\n"
        + "Approval Date: 08/07/2026\n"
        + "Manager signature present\n"
    )
    th = Thread(
        subject="June",
        items=[
            Item(key="A1", name="June.pdf", mime="application/pdf",
                 msg_index=0, text=unsigned, size=100),
            Item(key="A2", name="June-1.pdf", mime="application/pdf",
                 msg_index=2, text=signed, size=110),
        ],
    )
    _dedupe_sheet_items(th)
    assert {it.key for it in th.items} == {"A1", "A2"}
    assert th.dropped == []


def test_email_bodies_are_never_deduped_against_attachments():
    body = "Please find attached timesheets for June 2026."
    th = Thread(
        subject="June",
        items=[
            Item(key="A1", name="email body (message 1)", mime="text/plain",
                 msg_index=0, text=body, size=40),
            Item(key="A2", name="June.pdf", mime="application/pdf",
                 msg_index=0, text=_SHEET_CORE + body, size=200),
            Item(key="A3", name="email body (message 2)", mime="text/plain",
                 msg_index=1, text=body, size=40),
        ],
    )
    _dedupe_sheet_items(th)
    assert {it.key for it in th.items} == {"A1", "A2", "A3"}
    assert th.dropped == []


def test_same_template_different_employees_in_filename_is_never_deduped():
    """A bulk send of one identical timesheet template, one file per person,
    must never collapse two different named employees into one. (Originally
    a regression test for a bug where near-identical-text matching merged
    different employees' same-template sheets; that whole matching path is
    gone now — this keeps testing the invariant itself: distinct employees'
    sheets are never treated as duplicates, byte-identical or otherwise.)"""
    th = Thread(
        subject="June",
        items=[
            Item(key="A1", name="Employee_One_(11111)_hash.pdf", mime="application/pdf",
                 msg_index=0, text=_SHEET_CORE, size=100),
            Item(key="A2", name="Employee_Two_(22222)_hash.pdf", mime="application/pdf",
                 msg_index=1, text=_SHEET_CORE, size=100),
        ],
    )
    _dedupe_sheet_items(th)
    assert {it.key for it in th.items} == {"A1", "A2"}
    assert th.dropped == []


def test_same_employee_id_in_filename_also_keeps_both_approval_variants():
    """Same employee, two versions (unsigned v1 / signed v2 in the filename)
    — still both kept now, same as the general case above. Nothing about a
    shared employee id in the filename makes keyword-guessing any safer."""
    unsigned = _SHEET_CORE + "Status: Submitted\n"
    signed = _SHEET_CORE + "Approved By: Ahmed Shukri\nManager signature present\n"
    th = Thread(
        subject="June",
        items=[
            Item(key="A1", name="Ayaz_Kardame_(11111)_v1.pdf", mime="application/pdf",
                 msg_index=0, text=unsigned, size=100),
            Item(key="A2", name="Ayaz_Kardame_(11111)_v2.pdf", mime="application/pdf",
                 msg_index=2, text=signed, size=110),
        ],
    )
    _dedupe_sheet_items(th)
    assert {it.key for it in th.items} == {"A1", "A2"}
    assert th.dropped == []


def test_unrelated_sheets_are_not_merged():
    th = Thread(
        subject="mixed",
        items=[
            Item(key="A1", name="ayaz-june.pdf", mime="application/pdf",
                 msg_index=0, text=_SHEET_CORE, size=100),
            Item(key="A2", name="other-person.pdf", mime="application/pdf",
                 msg_index=0,
                 text=("Employee: Someone Else\nPeriod: May 2026\n"
                       "Project: Other\nDays: leave only\nTotal hours: 8\n"),
                 size=90),
        ],
    )
    _dedupe_sheet_items(th)
    assert {it.key for it in th.items} == {"A1", "A2"}
    assert th.dropped == []


# --------------------------------------------------------------------------
# Cache-aware extraction: reusing what a past run already found
# --------------------------------------------------------------------------

def _cached_entry(sheet: dict) -> dict:
    return {"filename": sheet["name"], "at": "2026-01-01T00:00:00+00:00",
            "model": "test-model", "prompt_version": "thread-v1", "sheet": sheet}


def _sheet_dict(name: str, **overrides) -> dict:
    base = {
        "name": name, "kind": "timesheet", "employee_name": "Cached Person",
        "employee_id": "C1", "month": 6, "year": 2026, "days_covered": 1,
        "period_type": "partial", "missing_days": [], "working_days": [],
        "weekend_days": [], "uncertain_days": [], "annual": ["2026-06-01"],
        "remote": [], "sick": [], "maternity": [], "unpaid": [], "absent": [],
        "public_holiday": [], "manager_signature": False, "approval_evidence": "",
        "text": "", "notes": "",
    }
    base.update(overrides)
    return base


async def test_sheet_name_is_the_real_attachment_not_the_a_number_label(mock_vision_calls):
    """The final sheet's "name" (shown in staging, Compare & Fix, and the
    debug view) must be the real filename a reviewer recognises, not the
    internal "[A#]" label the model echoes back — "[A1]" means nothing to a
    human, "june-timesheet.pdf" does. Caching (which resolves the ORIGINAL
    item back by that internal key, via the new `_source_key` field) must
    still work correctly once "name" stops being that key."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="See attached.", attachments=[
        ("june-timesheet.pdf", b"%PDF-1.4 fake sheet", "application", "pdf"),
    ])
    # Attachments get a key BEFORE the message body (added last, once every
    # message is walked) — one attachment here means it's "[A1]", the body
    # item is "[A2]".
    mock_vision_calls([
        {"thread_summary": "", "items": [{
            "source": "[A1]", "is_timesheet": True, "kind": "timesheet",
            "employee_name": "Test Person", "employee_id": "E1",
            "period_hint": "June 2026", "evidence": "1-June-26 present",
            "manager_signature": False, "signature_evidence": "", "notes": "",
        }]},
        {"sheets": [{
            "source": "[A1]", "employee_name": "Test Person", "employee_id": "E1",
            "month": 6, "year": 2026, "days_covered": 1, "period_type": "partial",
            "missing_days": [], "working_days": ["2026-06-01"], "weekend_days": [],
            "uncertain_days": [], "annual": [], "remote": [], "sick": [],
            "maternity": [], "unpaid": [], "absent": [], "public_holiday": [], "notes": "",
        }]},
    ])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    assert len(sheets) == 1
    assert sheets[0]["name"] == "june-timesheet.pdf"
    assert sheets[0]["_source_key"] == "[A1]"
    # Caching still resolves the item correctly via _source_key, not name.
    assert len(meta["_fresh_by_digest"]) == 1


def test_ui_pass_item_shows_the_real_name_not_the_a_number_label():
    """The live Extract UI's Pass 1 / "Timesheets for Pass 2" panels must
    show a name a reviewer recognises — "source" (the raw [A#] label) stays
    for debugging, but "name" is what the UI now renders."""
    from app.services.extract_email.thread_extract import _ui_pass_item

    items = [Item(key="A1", name="june-timesheet.pdf", mime="application/pdf", msg_index=0)]
    triage_item = {"source": "[A1]", "kind": "timesheet", "employee_name": "Test Person",
                   "employee_id": "E1", "period_hint": "June 2026",
                   "manager_signature": False, "notes": ""}

    ui_item = _ui_pass_item(triage_item, items)

    assert ui_item["source"] == "[A1]"       # unchanged — matches the model's own JSON
    assert ui_item["name"] == "june-timesheet.pdf"


async def test_noise_list_shows_real_names_not_a_number_labels(mock_vision_calls):
    """meta["noise"] (the "Model noise: ..." line in the live UI) must show
    real names too — a reviewer has no idea what "[A14]" refers to."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="See attached.", attachments=[
        ("marketing-flyer.pdf", b"%PDF-1.4 fake noise doc", "application", "pdf"),
    ])
    # The attachment gets key "A1" (before the message body).
    mock_vision_calls([{"thread_summary": "", "items": [{
        "source": "[A1]", "is_timesheet": False, "kind": "noise",
        "employee_name": None, "employee_id": None, "period_hint": "",
        "evidence": "", "manager_signature": False, "signature_evidence": "", "notes": "",
    }]}])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    assert meta["noise"] == ["marketing-flyer.pdf"]


async def test_cached_attachment_never_reaches_the_vision_model(mock_vision_calls):
    """A window-message attachment whose bytes are already cached is spliced
    in directly — pass 1/pass 2 never see it, only the body item does."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    pdf_bytes = b"%PDF-1.4 fake sheet"
    eml = _mail(plain="See attached.", attachments=[
        ("sheet.pdf", pdf_bytes, "application", "pdf"),
    ])
    digest = _content_digest(pdf_bytes)
    cached_sheet = _sheet_dict("sheet.pdf")
    cached = {digest: _cached_entry(cached_sheet)}

    # Only the body item can reach pass 1 this run — if the cached PDF leaked
    # through, a second scripted reply would be needed and this queue would
    # be exhausted (mock_vision_calls raises on an unscripted extra call).
    mock_vision_calls([{"thread_summary": "", "items": []}])

    sheets, approval, conflicts, meta = await extract_thread_sheets(
        [("msg 1", eml)], cached_sheets=cached)

    assert sheets == [cached_sheet]
    assert meta["reused_sheets"] == 1
    assert approval["detected"] is False


async def test_sheet_cached_from_outside_the_window_still_merges_in(mock_vision_calls):
    """A digest cached for a message that was never even fetched this run
    (the window only covers new + a couple of context messages) still shows
    up in the final result — coverage must never regress just because an
    older message wasn't re-read."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="Approved, thanks.")   # no attachment in THIS window at all
    external_sheet = _sheet_dict(
        "old-week1.pdf", period_type="week", days_covered=7,
        working_days=[f"2026-06-0{d}" for d in range(1, 8)], annual=[])
    cached = {"a" * 64: _cached_entry(external_sheet)}

    mock_vision_calls([{"thread_summary": "", "items": []}])
    sheets, approval, conflicts, meta = await extract_thread_sheets(
        [("msg 1", eml)], cached_sheets=cached)

    assert external_sheet in sheets
    assert meta["reused_sheets"] == 1


async def test_approval_signal_on_a_reused_sheet_is_not_lost(mock_vision_calls):
    """manager_signature on a cached sheet must still surface as a detected
    approval this run, even though nothing fresh confirmed it."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="fyi")
    signed_sheet = _sheet_dict(
        "signed.pdf", manager_signature=True, approval_evidence="D. Shetty")
    cached = {"b" * 64: _cached_entry(signed_sheet)}

    mock_vision_calls([{"thread_summary": "", "items": []}])
    sheets, approval, conflicts, meta = await extract_thread_sheets(
        [("msg 1", eml)], cached_sheets=cached)

    assert approval["detected"] is True
    assert "D. Shetty" in approval["evidence"]


async def test_named_only_signature_does_not_auto_detect_approval(mock_vision_calls):
    """A typed name next to 'Approved by:' with no signature/stamp/status mark
    (approval_named_only) must NOT flip detected — anyone can type a name.
    It still surfaces to the reviewer as weak evidence, not silence."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="fyi")
    named_only_sheet = _sheet_dict(
        "form.pdf", manager_signature=True, approval_evidence="John Smith",
        approval_named_only=True)
    cached = {"c" * 64: _cached_entry(named_only_sheet)}

    mock_vision_calls([{"thread_summary": "", "items": []}])
    sheets, approval, conflicts, meta = await extract_thread_sheets(
        [("msg 1", eml)], cached_sheets=cached)

    assert approval["detected"] is False
    assert approval["weak_evidence"] is True
    assert "John Smith" in approval["detail"]


async def test_a_real_signature_still_wins_alongside_a_weak_named_only_one(mock_vision_calls):
    """A genuinely strong signal elsewhere in the thread must not be
    suppressed just because a weaker, name-only item also exists."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="fyi")
    named_only_sheet = _sheet_dict(
        "form.pdf", manager_signature=True, approval_evidence="John Smith",
        approval_named_only=True)
    signed_sheet = _sheet_dict(
        "signed.pdf", manager_signature=True, approval_evidence="D. Shetty (stamped)")
    cached = {
        "c" * 64: _cached_entry(named_only_sheet),
        "d" * 64: _cached_entry(signed_sheet),
    }

    mock_vision_calls([{"thread_summary": "", "items": []}])
    sheets, approval, conflicts, meta = await extract_thread_sheets(
        [("msg 1", eml)], cached_sheets=cached)

    assert approval["detected"] is True
    assert "D. Shetty" in approval["evidence"]
    assert approval["weak_evidence"] is True, "the weak signal is still recorded, just not the deciding one"


async def test_nothing_fresh_confirmed_but_cache_has_content_still_returns_it(mock_vision_calls):
    """Pass 1 confirming zero NEW sheets must not make the run look empty when
    reused sheets exist — this is the exact "new reply with no attachment,
    everything else already extracted" scenario."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="Approved.")
    reused = _sheet_dict("earlier.pdf")
    cached = {"c" * 64: _cached_entry(reused)}

    mock_vision_calls([{"thread_summary": "", "items": [
        {"source": "email body (message 1)", "is_timesheet": False, "kind": "approval",
         "evidence": "", "manager_signature": False, "signature_evidence": "", "notes": ""},
    ]}])
    sheets, approval, conflicts, meta = await extract_thread_sheets(
        [("msg 1", eml)], cached_sheets=cached)

    assert sheets == [reused]
    assert meta["sheet_count"] == 1


async def test_no_cache_behaves_exactly_like_before(mock_vision_calls):
    """cached_sheets omitted (the default) must be identical to today: every
    attachment in the window goes through pass 1 fresh."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(attachments=[("sheet.pdf", b"%PDF-1.4 x", "application", "pdf")])
    mock_vision_calls([
        {"thread_summary": "", "items": [{
            "source": "sheet.pdf", "is_timesheet": True, "kind": "timesheet",
            "employee_name": "Fresh Person", "employee_id": "F1",
            "period_hint": "June 2026", "evidence": "1-June-26",
            "manager_signature": False, "signature_evidence": "", "notes": "",
        }]},
        {"sheets": [{
            "source": "sheet.pdf", "employee_name": "Fresh Person", "employee_id": "F1",
            "month": 6, "year": 2026, "days_covered": 1, "period_type": "partial",
            "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
            "annual": ["2026-06-01"], "remote": [], "sick": [], "maternity": [],
            "unpaid": [], "absent": [], "public_holiday": [], "notes": "",
        }]},
    ])
    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])
    assert len(sheets) == 1
    assert sheets[0]["employee_name"] == "Fresh Person"
    assert meta.get("reused_sheets", 0) == 0


async def test_multi_employee_roster_is_skipped_before_pass2(mock_vision_calls):
    """A roster-style attachment covering many employees in one grid must
    never reach pass 2's per-person reader (it would just pick one row and
    silently drop everyone else) — it belongs on the Bulk Upload page
    instead. Only ONE scripted reply is queued here (pass 1); if pass 2 were
    invoked anyway, mock_vision_calls would raise on the unscripted call."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="See attached.", attachments=[
        ("august-roster.xlsx", b"fake roster bytes", "application",
         "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ])
    mock_vision_calls([{"thread_summary": "", "items": [{
        "source": "[A1]", "is_timesheet": True, "kind": "timesheet",
        "employee_name": None, "employee_id": None,
        "covers_multiple_employees": True, "employee_row_count": 42,
        "period_hint": "August 2026", "evidence": "one grid, many named rows",
        "manager_signature": False, "signature_evidence": "", "notes": "",
    }]}])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    assert sheets == []
    assert meta["multi_employee_skipped"] == ["august-roster.xlsx (~42 employees)"]


# --------------------------------------------------------------------------
# Pass 2 retry when a confirmed, non-empty batch comes back with nothing
# --------------------------------------------------------------------------

async def test_pass2_retries_once_when_confirmed_batch_returns_no_sheets(mock_vision_calls):
    """Regression for a real bug: pass 1 confirmed a real timesheet (it read
    the same image successfully), but pass 2's first reply was a flatly
    empty `{"sheets": []}` — a bad/degenerate model generation, not evidence
    the document is blank. That must be retried once rather than accepted
    at face value and reported as "nothing found"."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="See attached.", attachments=[
        ("june-timesheet.pdf", b"%PDF-1.4 fake sheet", "application", "pdf"),
    ])
    mock_vision_calls([
        {"thread_summary": "", "items": [{
            "source": "[A1]", "is_timesheet": True, "kind": "timesheet",
            "employee_name": "Test Person", "employee_id": "E1",
            "period_hint": "June 2026", "evidence": "1-June-26 present",
            "manager_signature": False, "signature_evidence": "", "notes": "",
        }]},
        {"sheets": []},  # first pass-2 attempt: empty (the bug)
        {"sheets": [{  # retry: the real content comes through
            "source": "[A1]", "employee_name": "Test Person", "employee_id": "E1",
            "month": 6, "year": 2026, "days_covered": 1, "period_type": "partial",
            "missing_days": [], "working_days": ["2026-06-01"], "weekend_days": [],
            "uncertain_days": [], "annual": [], "remote": [], "sick": [],
            "maternity": [], "unpaid": [], "absent": [], "public_holiday": [], "notes": "",
        }]},
    ])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    assert len(sheets) == 1
    assert sheets[0]["employee_name"] == "Test Person"
    assert meta["calls"] == 3  # 1 pass-1 call + 2 pass-2 calls (original + retry)


async def test_pass2_does_not_retry_more_than_once(mock_vision_calls):
    """If the retry ALSO comes back empty, accept it — no infinite retries,
    and the confirmed item legitimately ends up unresolved rather than
    hanging the run."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="See attached.", attachments=[
        ("june-timesheet.pdf", b"%PDF-1.4 fake sheet", "application", "pdf"),
    ])
    mock_vision_calls([
        {"thread_summary": "", "items": [{
            "source": "[A1]", "is_timesheet": True, "kind": "timesheet",
            "employee_name": "Test Person", "employee_id": "E1",
            "period_hint": "June 2026", "evidence": "1-June-26 present",
            "manager_signature": False, "signature_evidence": "", "notes": "",
        }]},
        {"sheets": []},  # first attempt: empty
        {"sheets": []},  # retry: still empty — must be accepted, not retried again
    ])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    assert sheets == []
    assert meta["calls"] == 3


# --------------------------------------------------------------------------
# Resilience — one batch's call ultimately failing (its OWN internal
# retries in vision_client.chat_call already exhausted) must not discard
# every OTHER batch's results for the same thread. A long "reminder + many
# independent replies" thread needs several sequential pass-1/pass-2 calls;
# losing everything because call #4 of 7 hit a transient error would mean a
# 30-person thread interrupted once gets NOTHING staged, ever, until a
# perfect uninterrupted run — exactly the "stuck, lost it all" symptom this
# guards against.
# --------------------------------------------------------------------------

async def test_one_pass1_batch_failing_does_not_lose_other_batches_results(mock_vision_calls, monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    # One item per pass-1 call, so a single call failing costs exactly one item.
    monkeypatch.setattr(settings, "pass1_batch_size", 1)
    eml = _mail(plain="See attached.", attachments=[
        (f"sheet{i}.pdf", f"%PDF-1.4 fake sheet {i}".encode(), "application", "pdf")
        for i in range(1, 7)
    ])
    # items: sheet1..sheet6, then the body -> 7 single-item calls. The fixture
    # fills a verdict ("other") for any item a scripted reply leaves out.
    mock_vision_calls([
        RuntimeError("simulated transient failure"),             # sheet1 - fails outright
        {"thread_summary": "", "items": [{                       # sheet2 - succeeds
            "source": "[A2]", "is_timesheet": True, "kind": "timesheet",
            "employee_name": "Test Person", "employee_id": "E1",
            "period_hint": "June 2026", "evidence": "1-June-26 present",
            "manager_signature": False, "signature_evidence": "", "notes": "",
        }]},
        {"thread_summary": "", "items": []},   # sheet3
        {"thread_summary": "", "items": []},   # sheet4
        {"thread_summary": "", "items": []},   # sheet5
        {"thread_summary": "", "items": []},   # sheet6
        {"thread_summary": "", "items": []},   # body
        {"sheets": [{
            "source": "[A2]", "employee_name": "Test Person", "employee_id": "E1",
            "month": 6, "year": 2026, "days_covered": 1, "period_type": "partial",
            "missing_days": [], "working_days": ["2026-06-01"], "weekend_days": [],
            "uncertain_days": [], "annual": [], "remote": [], "sick": [],
            "maternity": [], "unpaid": [], "absent": [], "public_holiday": [], "notes": "",
        }]},
    ])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    # sheet2's result is still returned - the failure on sheet1 didn't wipe the run.
    assert len(sheets) == 1
    assert sheets[0]["name"] == "sheet2.pdf"
    assert meta["errors"], "the failure must be recorded, not silently swallowed"
    assert any("sheet1.pdf" in e for e in meta["errors"])
    assert meta["incomplete"] is True


async def test_failed_pass1_batch_is_split_and_every_item_still_classified(mock_vision_calls, monkeypatch):
    """A pass-1 call that fails with several items is split in half and retried,
    so one bad call no longer costs all of its items."""
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    monkeypatch.setattr(settings, "pass1_batch_size", 4)
    eml = _mail(plain="See attached.", attachments=[
        (f"sheet{i}.pdf", f"%PDF-1.4 fake sheet {i}".encode(), "application", "pdf") for i in (1, 2, 3)])
    # 3 sheets + body = 4 items in one batch.
    mock_vision_calls([
        RuntimeError("whole batch failed"),
        {"thread_summary": "", "items": [_p1_item("[A1]", "Person 1", "E1")]},    # first half (A1, A2)
        {"thread_summary": "", "items": []},                                       # second half (A3, body)
        {"sheets": [_p2_sheet("[A1]", "Person 1", "E1")]},
    ])
    sheets, _a, _c, meta = await extract_thread_sheets([("msg 1", eml)])
    assert [s["employee_name"] for s in sheets] == ["Person 1"]
    assert not meta["errors"], meta["errors"]
    assert meta["incomplete"] is False


async def test_item_missing_from_pass1_reply_is_asked_again_on_its_own(mock_vision_calls, monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    eml = _mail(plain="See attached.", attachments=[
        ("a.pdf", b"%PDF-1.4 fake a", "application", "pdf"),
        ("b.pdf", b"%PDF-1.4 fake b", "application", "pdf")])
    mock_vision_calls([
        # The reply covers A1 and the body but silently forgets A2 (b.pdf)...
        {"thread_summary": "", "items": [_p1_item("[A1]", "Person 1", "E1"),
                                         {"source": "[A3]", "kind": "other"}]},
        # ...so it is asked about on its own and turns out to be a timesheet.
        {"thread_summary": "", "items": [_p1_item("[A2]", "Person 2", "E2")]},
        {"sheets": [_p2_sheet("[A1]", "Person 1", "E1"), _p2_sheet("[A2]", "Person 2", "E2")]},
    ])
    sheets, _a, _c, meta = await extract_thread_sheets([("msg 1", eml)])
    assert sorted(s["employee_name"] for s in sheets) == ["Person 1", "Person 2"]
    assert not meta["errors"], meta["errors"]


async def test_pass2_item_dropped_from_reply_is_recovered_individually(mock_vision_calls, monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    names = ["Person 1", "Person 2", "Person 3"]
    eml = _mail(plain="See attached.", attachments=[
        (f"sheet{i}.pdf", f"%PDF-1.4 fake sheet {i}".encode(), "application", "pdf") for i in (1, 2, 3)])
    mock_vision_calls([
        {"thread_summary": "", "items": [_p1_item(f"[A{i}]", n, f"E{i}") for i, n in enumerate(names, 1)]},
        # The model transcribes two of the three and silently skips A2.
        {"sheets": [_p2_sheet("[A1]", "Person 1", "E1"), _p2_sheet("[A3]", "Person 3", "E3")]},
        {"sheets": [_p2_sheet("[A2]", "Person 2", "E2")]},      # the re-read of A2 alone
    ])
    sheets, _a, _c, meta = await extract_thread_sheets([("msg 1", eml)])
    assert sorted(s["employee_name"] for s in sheets) == names
    assert not meta["errors"], meta["errors"]


async def test_pass2_item_that_never_comes_back_makes_the_run_incomplete(mock_vision_calls, monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    eml = _mail(plain="See attached.", attachments=[
        ("sheet1.pdf", b"%PDF-1.4 fake sheet 1", "application", "pdf"),
        ("sheet2.pdf", b"%PDF-1.4 fake sheet 2", "application", "pdf")])
    mock_vision_calls([
        {"thread_summary": "", "items": [_p1_item("[A1]", "Person 1", "E1"),
                                         _p1_item("[A2]", "Person 2", "E2")]},
        {"sheets": [_p2_sheet("[A1]", "Person 1", "E1")]},
        {"sheets": []},                                       # re-read of A2 alone: still nothing
    ])
    sheets, _a, _c, meta = await extract_thread_sheets([("msg 1", eml)])
    assert [s["employee_name"] for s in sheets] == ["Person 1"]
    assert meta["incomplete"] is True
    assert any("sheet2.pdf" in e for e in meta["errors"]), meta["errors"]


async def test_picture_filed_as_other_gets_a_second_look(monkeypatch):
    """A screenshot the quick first pass called 'other' is shown again, alone, and
    a positive verdict from that second look wins."""
    from app.core.config import settings
    from app.services.extract_email import thread_prompt, triage_prompt
    from app.services.extract_email.thread_extract import extract_thread_sheets
    from app.services.extraction import vision_client

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    labels: list[str] = []

    async def fake_chat(system_prompt, blocks, model, api_key, *, label="", **kw):
        labels.append(label)
        if label.startswith("pass1-rescue"):
            return {"thread_summary": "", "items": [_p1_item("[A1]", "Person 1", "E1")]}
        if label.startswith("pass1"):
            return {"thread_summary": "", "items": [{"source": "[A1]", "kind": "other"},
                                                    {"source": "[A2]", "kind": "other"}]}
        return {"sheets": [_p2_sheet("[A1]", "Person 1", "E1")]}

    monkeypatch.setattr(vision_client, "chat_call", fake_chat)
    monkeypatch.setattr(triage_prompt, "chat_call", fake_chat)
    monkeypatch.setattr(thread_prompt, "chat_call", fake_chat)

    eml = _mail(plain="See attached.", attachments=[("week1.png", _png_bytes(big=True), "image", "png")])
    sheets, _a, _c, meta = await extract_thread_sheets([("msg 1", eml)])
    assert any(lbl.startswith("pass1-rescue") for lbl in labels)
    assert [s["employee_name"] for s in sheets] == ["Person 1"]


async def _run_with_scripted_kinds(monkeypatch, eml, kinds_by_label_prefix):
    """Run extract_thread_sheets with a scripted model; returns (labels, sheets, meta)."""
    from app.core.config import settings
    from app.services.extract_email import thread_prompt, triage_prompt
    from app.services.extract_email.thread_extract import extract_thread_sheets
    from app.services.extraction import vision_client

    monkeypatch.setattr(settings, "pass1_batch_size", 10)
    labels: list[str] = []

    async def fake_chat(system_prompt, blocks, model, api_key, *, label="", **kw):
        labels.append(label)
        for prefix, reply in kinds_by_label_prefix:
            if label.startswith(prefix):
                return reply
        raise AssertionError(f"unexpected model call {label}")

    monkeypatch.setattr(vision_client, "chat_call", fake_chat)
    monkeypatch.setattr(triage_prompt, "chat_call", fake_chat)
    monkeypatch.setattr(thread_prompt, "chat_call", fake_chat)
    sheets, _a, _c, meta = await extract_thread_sheets([("msg 1", eml)])
    return labels, sheets, meta


async def test_leave_request_in_an_email_body_filed_as_other_gets_a_second_look(monkeypatch):
    """A leave request written only in the message text (no attachment) that the
    quick first pass called 'other' is re-checked alone, and the positive verdict wins."""
    eml = _mail(plain="Hi, I will be on sick leave from 3 to 5 June 2026. Thanks, Person 1")
    labels, sheets, _meta = await _run_with_scripted_kinds(monkeypatch, eml, [
        ("pass1-rescue", {"thread_summary": "", "items": [{**_p1_item("[A1]", "Person 1", "E1"),
                                                             "kind": "leave_certificate"}]}),
        ("pass1", {"thread_summary": "", "items": [{"source": "[A1]", "kind": "other"}]}),
        ("pass2", {"sheets": [_p2_sheet("[A1]", "Person 1", "E1")]}),
    ])
    assert any(lbl.startswith("pass1-rescue") for lbl in labels), labels
    assert [s["employee_name"] for s in sheets] == ["Person 1"]


async def test_ordinary_conversation_and_unrelated_files_are_ignored_cheaply(monkeypatch):
    """Plain chat with no leave vocabulary is not re-read, nothing is extracted,
    and nothing is reported as a problem."""
    eml = _mail(subject="Lunch on Friday", plain="Shall we get lunch on Friday? The new place opened.")
    labels, sheets, meta = await _run_with_scripted_kinds(monkeypatch, eml, [
        ("pass1", {"thread_summary": "A lunch invitation.",
                   "items": [{"source": "[A1]", "kind": "other"}]}),
    ])
    assert sheets == []
    assert not any(lbl.startswith("pass1-rescue") for lbl in labels), labels
    assert not any(lbl.startswith("pass2") for lbl in labels), labels
    assert meta["incomplete"] is False and not meta["errors"]


async def test_an_invoice_attachment_is_checked_again_but_stays_ignored(monkeypatch):
    """An unrelated PDF gets one cheap second look (it carries pictures/text), the
    model confirms 'other', and no sheet is produced."""
    eml = _mail(plain="Invoice attached.", attachments=[
        ("invoice.pdf", b"%PDF-1.4 fake invoice", "application", "pdf")])
    labels, sheets, meta = await _run_with_scripted_kinds(monkeypatch, eml, [
        ("pass1-rescue", {"thread_summary": "", "items": [{"source": "[A1]", "kind": "other"}]}),
        ("pass1", {"thread_summary": "An invoice.",
                   "items": [{"source": "[A1]", "kind": "other"}, {"source": "[A2]", "kind": "other"}]}),
    ])
    assert sheets == []
    assert not any(lbl.startswith("pass2") for lbl in labels)
    assert meta["incomplete"] is False


async def test_one_pass2_batch_failing_does_not_lose_other_batches_results(mock_vision_calls, monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    # Force each confirmed sheet into its OWN pass-2 batch (1 image each),
    # so batch 1 (sheet A) and batch 2 (sheet B) are independent calls.
    monkeypatch.setattr(settings, "max_images_per_call", 1)

    eml = _mail(plain="See attached.", attachments=[
        ("sheetA.pdf", b"%PDF-1.4 fake sheet A", "application", "pdf"),
        ("sheetB.pdf", b"%PDF-1.4 fake sheet B", "application", "pdf"),
    ])

    mock_vision_calls([
        {"thread_summary": "", "items": [
            {"source": "[A1]", "is_timesheet": True, "kind": "timesheet",
             "employee_name": "Person A", "employee_id": "EA", "period_hint": "June 2026",
             "evidence": "1-June-26 present", "manager_signature": False,
             "signature_evidence": "", "notes": ""},
            {"source": "[A2]", "is_timesheet": True, "kind": "timesheet",
             "employee_name": "Person B", "employee_id": "EB", "period_hint": "June 2026",
             "evidence": "1-June-26 present", "manager_signature": False,
             "signature_evidence": "", "notes": ""},
        ]},
        RuntimeError("simulated pass2 failure for sheet A"),   # batch 1 (sheet A) — fails outright
        {"sheets": [{                                          # batch 2 (sheet B) — succeeds
            "source": "[A2]", "employee_name": "Person B", "employee_id": "EB",
            "month": 6, "year": 2026, "days_covered": 1, "period_type": "partial",
            "missing_days": [], "working_days": ["2026-06-01"], "weekend_days": [],
            "uncertain_days": [], "annual": [], "remote": [], "sick": [],
            "maternity": [], "unpaid": [], "absent": [], "public_holiday": [], "notes": "",
        }]},
    ])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    assert len(sheets) == 1
    assert sheets[0]["employee_name"] == "Person B"
    assert meta["errors"], "sheet A's failure must be recorded"
    assert any("Pass 2 batch 1" in e for e in meta["errors"])


async def test_pass2_batch_whose_retry_also_fails_is_recorded_not_raised(mock_vision_calls):
    """The existing "empty result -> retry once" path can ALSO hit a real
    failure on that retry attempt — must be caught the same way as a
    first-attempt failure, not left to propagate and abort everything."""
    from app.services.extract_email.thread_extract import extract_thread_sheets

    eml = _mail(plain="See attached.", attachments=[
        ("june-timesheet.pdf", b"%PDF-1.4 fake sheet", "application", "pdf"),
    ])
    mock_vision_calls([
        {"thread_summary": "", "items": [{
            "source": "[A1]", "is_timesheet": True, "kind": "timesheet",
            "employee_name": "Test Person", "employee_id": "E1",
            "period_hint": "June 2026", "evidence": "1-June-26 present",
            "manager_signature": False, "signature_evidence": "", "notes": "",
        }]},
        {"sheets": []},                              # first attempt: empty -> triggers retry
        RuntimeError("simulated retry failure"),      # retry: fails outright
    ])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    assert sheets == []
    assert meta["errors"]
    assert any("june-timesheet.pdf" in e and "could not be read" in e for e in meta["errors"])
    assert meta["incomplete"] is True


def test_collect_thread_strips_nul_from_attachment_and_nested_message_filenames():
    """A malformed RFC 2231 `filename*=` can decode into a real NUL byte.
    Postgres json/jsonb accepts the \u0000 escape on write but raises on any
    later read of that document, so neither a top-level attachment name nor a
    nested forwarded-message name (which lands in opened_containers ->
    extraction_meta) may carry one."""
    import base64

    pdf = base64.b64encode(b"%PDF-1.4\n" + b"0" * 5000)
    inner = (b"From: x@y.z\r\nSubject: inner\r\nMIME-Version: 1.0\r\n"
             b"Content-Type: text/plain\r\n\r\ninner body\r\n")
    raw = (b"From: a@b.c\r\nTo: d@e.f\r\nSubject: t\r\nMIME-Version: 1.0\r\n"
           b'Content-Type: multipart/mixed; boundary="BB"\r\n\r\n'
           b"--BB\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
           b"--BB\r\nContent-Type: application/pdf\r\n"
           b"Content-Disposition: attachment; filename*=utf-8''sheet%00one.pdf\r\n"
           b"Content-Transfer-Encoding: base64\r\n\r\n" + pdf + b"\r\n"
           b"--BB\r\nContent-Type: message/rfc822\r\n"
           b"Content-Disposition: attachment; filename*=utf-8''fwd%00mail.eml\r\n\r\n"
           + inner + b"\r\n--BB--\r\n")

    th = collect_thread([("msg 1", raw)])

    names = [i.name for i in th.items] + [c["name"] for c in th.opened_containers]
    assert "sheetone.pdf" in names and "fwdmail.eml" in names
    assert all("\x00" not in n for n in names)


def _p2_sheet(src, name, emp_id):
    return {"source": src, "employee_name": name, "employee_id": emp_id, "month": 10, "year": 2026,
            "days_covered": 1, "period_type": "partial", "missing_days": [],
            "working_days": ["2026-10-01"], "weekend_days": [], "uncertain_days": [],
            "annual": [], "remote": [], "sick": [], "maternity": [], "unpaid": [], "absent": [],
            "public_holiday": [], "notes": ""}


def _p1_item(src, name, emp_id):
    return {"source": src, "is_timesheet": True, "kind": "timesheet", "employee_name": name,
            "employee_id": emp_id, "period_hint": "October 2026", "evidence": "1-Oct-26 present",
            "manager_signature": False, "signature_evidence": "", "notes": ""}


async def test_pass2_calls_are_capped_by_sheet_count_not_only_images(mock_vision_calls, monkeypatch):
    """14 one-image timesheets in one email used to become a 12-sheet call whose
    reply overran the output limit and was lost. With a per-call sheet cap they
    go out in small batches and every sheet is read."""
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    monkeypatch.setattr(settings, "pass2_max_sheets_per_call", 3)
    monkeypatch.setattr(settings, "pass1_batch_size", 10)   # keep pass 1 to ONE call so the scripted replies line up
    names = [f"Person {i}" for i in range(1, 8)]
    eml = _mail(plain="See attached.", attachments=[
        (f"sheet{i}.pdf", f"%PDF-1.4 fake sheet {i}".encode(), "application", "pdf") for i in range(1, 8)])
    mock_vision_calls([
        {"thread_summary": "", "items": [_p1_item(f"[A{i}]", n, f"E{i}") for i, n in enumerate(names, 1)]},
        {"sheets": [_p2_sheet(f"[A{i}]", names[i - 1], f"E{i}") for i in (1, 2, 3)]},   # batch 1: 3 sheets
        {"sheets": [_p2_sheet(f"[A{i}]", names[i - 1], f"E{i}") for i in (4, 5, 6)]},   # batch 2: 3 sheets
        {"sheets": [_p2_sheet("[A7]", names[6], "E7")]},                                  # batch 3: 1 sheet
    ])
    sheets, _approval, _conf, meta = await extract_thread_sheets([("msg 1", eml)])
    assert len(sheets) == 7, meta["errors"]
    assert not meta["errors"]


async def test_a_failed_pass2_batch_is_split_and_retried_not_lost(mock_vision_calls, monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    monkeypatch.setattr(settings, "pass2_max_sheets_per_call", 4)
    names = [f"Person {i}" for i in range(1, 5)]
    eml = _mail(plain="See attached.", attachments=[
        (f"sheet{i}.pdf", f"%PDF-1.4 fake sheet {i}".encode(), "application", "pdf") for i in range(1, 5)])
    mock_vision_calls([
        {"thread_summary": "", "items": [_p1_item(f"[A{i}]", n, f"E{i}") for i, n in enumerate(names, 1)]},
        RuntimeError("pass2-batch1 failed after 3 attempts: unparseable JSON reply"),     # all 4 in one call: cut off
        {"sheets": [_p2_sheet(f"[A{i}]", names[i - 1], f"E{i}") for i in (1, 2)]},        # first half
        {"sheets": [_p2_sheet(f"[A{i}]", names[i - 1], f"E{i}") for i in (3, 4)]},        # second half
    ])
    sheets, _approval, _conf, meta = await extract_thread_sheets([("msg 1", eml)])
    assert sorted(s["employee_name"] for s in sheets) == names
    assert not meta["errors"], "a batch that recovers by splitting must not be reported as an error"


async def test_split_that_still_fails_on_one_sheet_reports_only_that_sheet(mock_vision_calls, monkeypatch):
    from app.core.config import settings
    from app.services.extract_email.thread_extract import extract_thread_sheets

    monkeypatch.setattr(settings, "pass2_max_sheets_per_call", 2)
    names = ["Person 1", "Person 2"]
    eml = _mail(plain="See attached.", attachments=[
        (f"sheet{i}.pdf", f"%PDF-1.4 fake sheet {i}".encode(), "application", "pdf") for i in (1, 2)])
    mock_vision_calls([
        {"thread_summary": "", "items": [_p1_item(f"[A{i}]", n, f"E{i}") for i, n in enumerate(names, 1)]},
        RuntimeError("whole batch failed"),
        {"sheets": [_p2_sheet("[A1]", "Person 1", "E1")]},          # half a: ok
        RuntimeError("sheet 2 unreadable"),                          # half b: fails
    ])
    sheets, _approval, _conf, meta = await extract_thread_sheets([("msg 1", eml)])
    assert [s["employee_name"] for s in sheets] == ["Person 1"]
    assert any("sheet2.pdf" in e and "even after splitting" in e for e in meta["errors"])
