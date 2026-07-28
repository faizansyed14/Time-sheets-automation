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


def test_tiny_images_are_dropped_before_any_ocr_or_model_call():
    """A signature icon / tracking pixel never reaches the model — filtered by
    size alone, before OCR is even attempted."""
    eml = _mail(attachments=[
        ("logo.png", _png_bytes(big=False), "image", "png"),
    ])
    th = collect_thread([("msg 1", eml)])
    assert not any(it.name == "logo.png" for it in th.items)
    assert any(d["name"] == "logo.png" for d in th.dropped)


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


def test_near_same_ocr_prefers_manager_signed_copy():
    """Unsigned first send + later regen with Approved By → keep signed one."""
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
    assert [it.key for it in th.items] == ["A2"]
    assert th.dropped[0]["filter"] == "dup"
    assert th.dropped[0]["kept_key"] == "A2"
    assert "approval" in th.dropped[0]["reason"].lower() or "same" in th.dropped[0]["reason"].lower()


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
    must never collapse two different named employees into one — even though
    the shared boilerplate layout scores as "near-identical" text. Regression
    for a real bug: 5 of 10 employees in one email were silently dropped as
    "duplicates" of each other because their sheets used the same template."""
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


def test_same_employee_id_in_filename_still_dedupes_regenerated_copy():
    """The guard must not block the ORIGINAL, legitimate case it sits next to:
    the same employee's own sheet resent/regenerated with a stronger
    signature — same id in both filenames, so dedup still collapses them."""
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
    assert [it.key for it in th.items] == ["A2"]
    assert th.dropped[0]["filter"] == "dup"
    assert th.dropped[0]["kept_key"] == "A2"


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
