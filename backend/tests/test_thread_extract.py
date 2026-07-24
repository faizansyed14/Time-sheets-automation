"""Two-pass thread extraction: what gets sent, and how each reply is used.

Extract Email runs TWO focused calls over one conversation:

  Pass 1 — understand: which items are really timesheets, whose each one is,
           whether a manager approved, what the thread is about, what is noise.
  Pass 2 — extract: read ONLY the validated sheets, transcribing leave.

One call asked to do both did neither well — sheets were invented from passing
mentions while real grids were skimmed. These tests cover the parts that must
hold without spending a call.
"""
from email.message import EmailMessage as MimeMessage

from app.services.extract_email.thread_extract import (
    ThreadPayload,
    collect_thread_payload,
    normalise_extraction,
    normalise_triage,
)
from app.services.extract_email.thread_prompt import build_extraction_prompt
from app.services.extract_email.triage_prompt import build_triage_prompt


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
# What gets sent
# --------------------------------------------------------------------------

def test_over_capacity_files_are_skipped_with_the_right_reason():
    """A file dropped for hitting MAX_FILES is a CAPACITY problem — a real,
    readable document. It must never be reported as 'unsupported type', or the
    actual fix (raise the cap / split the thread) never gets noticed."""
    import app.services.extract_email.thread_extract as te

    eml = _mail(attachments=[
        (f"sheet{i}.pdf", f"%PDF-1.4 sheet {i}".encode(), "application", "pdf")
        for i in range(te.MAX_FILES + 2)
    ])
    p = collect_thread_payload([("msg 1", eml)])

    assert len(p.files) == te.MAX_FILES
    reasons = {reason for _, reason in p.skipped}
    assert reasons == {"over_capacity"}
    assert len(p.skipped) == 2


def test_unsupported_type_is_a_different_reason_than_capacity():
    eml = _mail(attachments=[
        ("archive.zip", b"PK\x03\x04 fake zip", "application", "zip"),
    ])
    p = collect_thread_payload([("msg 1", eml)])
    assert p.files == [] and p.images == []
    assert p.skipped == [("archive.zip", "unsupported_type")]


def test_documents_and_real_sized_images_are_all_sent():
    """A real screenshot (well above MIN_IMAGE_BYTES) is sent untouched — pass 1
    names it as noise or evidence itself, which is far safer than a heuristic
    guessing 'signature icon' and dropping a real approval screenshot."""
    import app.services.extract_email.thread_extract as te

    eml = _mail(attachments=[
        ("timesheet.pdf", b"%PDF-1.4 fake sheet", "application", "pdf"),
        ("screenshot.png", b"\x89PNG\r\n\x1a\n" + b"0" * (te.MIN_IMAGE_BYTES + 5000),
         "image", "png"),
    ])
    p = collect_thread_payload([("msg 1", eml)])

    assert [n for n, _, _ in p.files] == ["timesheet.pdf"]
    assert [n for n, _ in p.images] == ["screenshot.png"], \
        "images at/above the size floor must not be filtered out"


def test_tiny_images_are_filtered_before_the_model_sees_them():
    """A signature icon / social-media button / tracking pixel is never a
    document worth a vision call — real screenshots run far larger than this.
    Filtered here (by size) rather than left for the model, and reported with
    its own skip reason so it is never confused with a capacity drop or an
    unreadable filetype."""
    import app.services.extract_email.thread_extract as te

    eml = _mail(attachments=[
        ("logo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 200, "image", "png"),
    ])
    p = collect_thread_payload([("msg 1", eml)])

    assert p.images == []
    assert p.skipped == [("logo.png", "too_small")]


def test_html_table_body_is_not_lost():
    """The pasted grid lives in text/html; text/plain carries only the note."""
    html = ("<html><body><p>Hi</p><table>"
            "<tr><td>1-June-26</td><td>Sick Leave</td></tr>"
            "<tr><td>2-June-26</td><td>Annual Leave</td></tr>"
            "</table></body></html>")
    p = collect_thread_payload([("msg 1", _mail(plain="Hi team", html=html))])

    assert "1-June-26" in p.bodies
    assert "Sick Leave" in p.bodies
    assert any("body grid" in n for n, _ in p.images), \
        f"a real <table> body should also render to an image: {[n for n, _ in p.images]}"


def test_plain_body_without_a_table_is_not_rendered():
    """Ordinary mail must not pay for a body render it doesn't need."""
    p = collect_thread_payload([("msg 1", _mail(plain="Approved. Thanks."))])
    assert not any("body grid" in n for n, _ in p.images)
    assert "Approved" in p.bodies


def test_identical_attachment_across_messages_is_sent_once():
    pdf = b"%PDF-1.4 the same sheet"
    a = _mail(attachments=[("sheet.pdf", pdf, "application", "pdf")])
    b = _mail(subject="RE: TIMESHEET", attachments=[("sheet-copy.pdf", pdf, "application", "pdf")])
    p = collect_thread_payload([("msg 1", a), ("msg 2", b)])
    assert len(p.files) == 1, [n for n, _, _ in p.files]


def test_every_message_body_is_included_oldest_first():
    a = _mail(subject="TIMESHEET June", plain="Here is my sheet.")
    b = _mail(subject="RE: TIMESHEET June", plain="Approved by me.")
    p = collect_thread_payload([("msg 1", a), ("msg 2", b)])
    assert p.bodies.index("Here is my sheet") < p.bodies.index("Approved by me")


# --------------------------------------------------------------------------
# Emails inside emails
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
        m.add_attachment(inner, maintype="application", subtype="octet-stream",
                         filename=filename)
    return m.as_bytes()


def test_eml_attached_as_a_file_is_opened():
    """An .eml attached as application/octet-stream is just bytes — MIME
    walking never sees inside it. Before this was handled, the timesheet it
    carried was silently dropped as an 'unsupported type'."""
    inner = _mail(subject="Inner", plain="inner body text",
                  attachments=[("hidden.pdf", b"%PDF-1.4 hidden", "application", "pdf")])
    p = collect_thread_payload([("msg 1", _eml_attached(inner))])

    assert [n for n, _, _ in p.files] == ["hidden.pdf"], \
        "the sheet inside the attached email must be recovered"
    assert "inner body text" in p.bodies
    assert p.skipped == []


def test_eml_nested_several_levels_deep_is_opened():
    """A forward of a forward of a forward still carries a real sheet."""
    deepest = _mail(subject="Deepest", plain="deep",
                    attachments=[("deep.pdf", b"%PDF-1.4 deep", "application", "pdf")])
    mid = _eml_attached(deepest, filename="level3.eml")
    top = _eml_attached(mid, filename="level2.eml")
    p = collect_thread_payload([("msg 1", top)])
    assert [n for n, _, _ in p.files] == ["deep.pdf"]


def test_eml_recursion_is_depth_limited():
    """A mail loop must not recurse forever."""
    import app.services.extract_email.thread_extract as te

    payload = _mail(subject="base", plain="base")
    for _ in range(te.MAX_EML_DEPTH + 3):
        payload = _eml_attached(payload)
    collect_thread_payload([("msg 1", payload)])   # must simply return


# --------------------------------------------------------------------------
# Pass 1 — the triage prompt
# --------------------------------------------------------------------------

def test_triage_prompt_demands_evidence_and_allows_an_empty_answer():
    _sys, user = build_triage_prompt(manifest=[], bodies="")
    assert "A NAME AND A MONTH ARE NOT ENOUGH" in user
    assert "empty `items` list" in user or '"items"' in user
    assert "invoice" in user.lower()


def test_triage_prompt_covers_all_three_places_approval_can_hide():
    _sys, user = build_triage_prompt(manifest=[], bodies="")
    assert "ON THE SHEET" in user
    assert "IN AN IMAGE" in user
    assert "IN THE CONVERSATION" in user
    assert "HANDWRITTEN SIGNATURE IMAGE COUNTS" in user
    # ...and still refuses to read a request as an approval.
    assert "please approve" in user.lower()


def test_triage_prompt_asks_for_noise_and_multiple_employees():
    _sys, user = build_triage_prompt(manifest=[], bodies="")
    assert "noise" in user.lower()
    assert "several employees" in user.lower() or "whole team" in user.lower()


def test_triage_prompt_lists_the_known_templates():
    _sys, user = build_triage_prompt(manifest=[], bodies="")
    assert "alpha_adr_attendance" in user


# --------------------------------------------------------------------------
# Pass 2 — the extraction prompt
# --------------------------------------------------------------------------

_TRIAGED = [{
    "source": "sheet.pdf", "kind": "timesheet", "format_id": "alpha_adr_attendance",
    "employee_name": "Bhargavi Prabhu", "employee_id": "E2506943",
    "period_hint": "June 2026", "evidence": "1-June-26 08:00 AM",
    "manager_signature": True, "signature_evidence": "D. Shetty", "notes": "",
}]


def test_extraction_prompt_is_told_not_to_reclassify():
    """Pass 1 already decided these are timesheets. Re-litigating that is how
    the single-call version ended up skimming real grids."""
    _sys, user = build_extraction_prompt(sheets=_TRIAGED, format_ids=["alpha_adr_attendance"])
    assert "ALREADY been confirmed" in user
    assert "do not question whether they are timesheets" in user
    assert "Bhargavi Prabhu" in user


def test_extraction_prompt_carries_only_matching_template_rules():
    _sys, user = build_extraction_prompt(sheets=_TRIAGED, format_ids=["alpha_adr_attendance"])
    assert "alpha_adr_attendance" in user
    assert "dewa_moro_smartoffice" not in user
    assert "gpssa_daily_report" not in user


def test_extraction_prompt_keeps_the_leave_mapping_and_medical_rule():
    _sys, user = build_extraction_prompt(sheets=_TRIAGED, format_ids=[])
    assert "MEDICAL" in user
    assert "IS SICK LEAVE" in user.upper()
    assert "NEVER DEFAULT TO ANNUAL" in user.upper()
    for label in ("LWP", "AWOL", "WFH", "Vacation", "Maternity"):
        assert label in user, f"{label} missing from the leave mapping"


def test_extraction_prompt_spells_out_the_messy_cases():
    """Partial months, missing days and empty columns are the normal state of
    a real sheet, not exceptions."""
    _sys, user = build_extraction_prompt(sheets=_TRIAGED, format_ids=[])
    for case in ("PARTIAL MONTH", "MISSING DAYS", "EMPTY COLUMNS",
                 "TWO-DIGIT YEARS", "OVERLAPPING ENTRIES", "TOTALS ROWS"):
        assert case in user, f"{case} not covered"


def test_extraction_prompt_lets_the_sheet_override_the_triaged_name():
    _sys, user = build_extraction_prompt(sheets=_TRIAGED, format_ids=[])
    assert "the SHEET wins" in user


# --------------------------------------------------------------------------
# Normalising pass 1
# --------------------------------------------------------------------------

def test_triage_without_evidence_is_not_a_timesheet():
    """"Please find my timesheet for June" with no grid used to open an empty
    Compare & Fix that a human then had to delete."""
    raw = {"items": [{
        "source": "email body", "is_timesheet": True, "kind": "timesheet",
        "employee_name": "Anfal Taj", "evidence": "",
    }]}
    items, _a, _s, _n = normalise_triage(raw, ThreadPayload())
    assert items[0]["kind"] == "other", "a name + a month is not a timesheet"


def test_triage_with_a_quoted_row_is_a_timesheet():
    raw = {"items": [{
        "source": "sheet.pdf", "kind": "timesheet",
        "evidence": "1-June-26 08:00 AM 5:00 PM",
    }]}
    items, _a, _s, _n = normalise_triage(raw, ThreadPayload())
    assert items[0]["kind"] == "timesheet"


def test_leave_certificate_needs_no_grid():
    """Only the timesheet claim is policed — certificates legitimately have no
    day rows."""
    raw = {"items": [{"source": "cert.pdf", "kind": "leave_certificate", "evidence": ""}]}
    items, _a, _s, _n = normalise_triage(raw, ThreadPayload())
    assert items[0]["kind"] == "leave_certificate"


def test_triage_captures_approval_and_where_it_came_from():
    raw = {"approval": {"detected": True, "evidence": "Approved — D. Shetty",
                        "source": "reply 2", "where": "conversation"}}
    _i, approval, _s, _n = normalise_triage(raw, ThreadPayload())
    assert approval["detected"] is True
    assert "D. Shetty" in approval["detail"]
    assert approval["where"] == "conversation"


def test_a_signed_sheet_counts_as_approval():
    """Observed: the model reported `manager_signature: true` on the sheet and
    `approval.detected: false` in the same answer. That contradiction holds an
    already-approved timesheet for review, so the per-item finding wins."""
    raw = {
        "items": [{"source": "sheet.pdf", "kind": "timesheet",
                   "evidence": "1-June-26 08:00 AM",
                   "manager_signature": True, "signature_evidence": "D. Shetty"}],
        "approval": {"detected": False},
    }
    _i, approval, _s, _n = normalise_triage(raw, ThreadPayload())
    assert approval["detected"] is True
    assert approval["where"] == "sheet"
    assert "D. Shetty" in approval["evidence"]


def test_an_unsigned_thread_stays_unapproved():
    raw = {
        "items": [{"source": "sheet.pdf", "kind": "timesheet",
                   "evidence": "1-June-26", "manager_signature": False}],
        "approval": {"detected": False},
    }
    _i, approval, _s, _n = normalise_triage(raw, ThreadPayload())
    assert approval["detected"] is False


def test_triage_summary_status_is_validated():
    raw = {"summary": {"headline": "x", "status": "nonsense", "narrative": "y"}}
    _i, _a, summary, _n = normalise_triage(raw, ThreadPayload())
    assert summary["status"] == "other"


def test_no_approval_reads_as_not_approved():
    _i, approval, _s, _n = normalise_triage({}, ThreadPayload())
    assert approval["detected"] is False
    assert "No manager approval" in approval["detail"]


def test_noise_is_reported():
    raw = {"noise": ["logo.png", "banner.jpg"]}
    _i, _a, _s, noise = normalise_triage(raw, ThreadPayload())
    assert noise == ["logo.png", "banner.jpg"]


# --------------------------------------------------------------------------
# Normalising pass 2 (merged with pass 1)
# --------------------------------------------------------------------------

def _payload_with_text(name: str, text: str) -> ThreadPayload:
    """A payload where `name` is a real attachment that also has extracted
    text — the shape the collector actually produces."""
    p = ThreadPayload()
    p.files = [(name, b"%PDF-1.4", "pdf")]
    p.texts[name] = text
    return p


# --------------------------------------------------------------------------
# Matching pass 1's source names to real payload items
# --------------------------------------------------------------------------

def test_email_body_resolves_to_the_rendered_body_grid():
    """Pass 1 is told to call the body "email body", but the render is stored
    as "<subject>.eml — body grid 1". Matching those literally found nothing,
    so pass 2 was handed no attachment and reported NO leave — silently,
    because an empty answer looks like a clean one."""
    from app.services.extract_email.thread_extract import resolve_source

    p = ThreadPayload()
    p.images = [("TIMESHEET June.eml — body grid 1", b"jpg"), ("logo.png", b"png")]

    assert resolve_source("email body", p) == ["TIMESHEET June.eml — body grid 1"]
    assert resolve_source("body", p) == ["TIMESHEET June.eml — body grid 1"]


def test_exact_attachment_name_resolves_to_itself():
    from app.services.extract_email.thread_extract import resolve_source

    p = ThreadPayload()
    p.files = [("sheet.pdf", b"x", "pdf")]
    assert resolve_source("sheet.pdf", p) == ["sheet.pdf"]


def test_unknown_source_resolves_to_nothing():
    from app.services.extract_email.thread_extract import resolve_source

    p = ThreadPayload()
    p.files = [("sheet.pdf", b"x", "pdf")]
    assert resolve_source("something-else.pdf", p) == []


def test_body_sheet_text_reaches_the_deterministic_gate():
    """auto-accept's day-coverage check reads the sheet's OWN text. With the
    name mismatch it got "" for body sheets, so coverage could never verify."""
    p = ThreadPayload()
    p.images = [("June.eml — body grid 1", b"jpg")]
    p.texts["June.eml — body grid 1"] = "1-June-26 08:00 AM"

    triaged = [{"source": "email body", "kind": "timesheet", "format_id": "generic"}]
    raw = {"sheets": [{"source": "email body", "month": 6, "year": 2026}]}
    s = normalise_extraction(raw, triaged, p)[0]
    assert s["text"] == "1-June-26 08:00 AM"


def test_extraction_prompt_includes_body_text_when_the_sheet_is_pasted():
    _sys, user = build_extraction_prompt(
        sheets=_TRIAGED, format_ids=[], body_text="1-June-26 | Maternity Leave")
    assert "PASTED INTO THE EMAIL BODY" in user
    assert "Maternity Leave" in user


def test_extraction_prompt_omits_the_body_block_when_not_needed():
    _sys, user = build_extraction_prompt(sheets=_TRIAGED, format_ids=[])
    assert "PASTED INTO THE EMAIL BODY" not in user


def test_extraction_merges_with_triage_into_the_staging_shape():
    raw = {"sheets": [{
        "source": "sheet.pdf",
        "employee_name": "Bhargavi Prabhu", "employee_id": "E2506943",
        "month": 6, "year": 2026, "days_covered": 30, "period_type": "full_month",
        "missing_days": [], "evidence": "1-June-26 08:00 AM",
        "sick": ["2026-06-19"], "public_holiday": ["2026-06-15"],
    }]}
    sheets = normalise_extraction(raw, _TRIAGED, _payload_with_text("sheet.pdf", "1-June-26 ..."))

    s = sheets[0]
    assert s["kind"] == "timesheet"                 # from pass 1
    assert s["format_id"] == "alpha_adr_attendance"  # from pass 1
    assert s["manager_signature"] is True            # from pass 1
    assert s["employee_id"] == "E2506943"
    assert (s["month"], s["year"]) == (6, 2026)
    assert s["buckets"]["sick"] == ["2026-06-19"]
    assert s["buckets"]["annual"] == []              # every bucket present
    assert s["dates_complete"] is True
    # Deterministic gates downstream read the sheet's OWN text, not the model's
    # claim about it — so it must be carried through.
    assert s["text"] == "1-June-26 ..."


def test_partial_sheet_is_flagged_incomplete():
    raw = {"sheets": [{
        "source": "sheet.pdf", "month": 6, "year": 2026,
        "days_covered": 15, "period_type": "half_month", "missing_days": [16, 17],
    }]}
    s = normalise_extraction(raw, _TRIAGED, ThreadPayload())[0]
    assert s["period_type"] == "half_month"
    assert s["dates_complete"] is False
    assert s["incomplete_sheet"] is True
    assert s["missing_days"] == [16, 17]


def test_garbage_values_are_rejected_not_passed_through():
    """A bad month/year must become None — never a wrong period on a payroll
    record."""
    raw = {"sheets": [{
        "source": "sheet.pdf", "month": 77, "year": 1200,
        "period_type": "whatever", "days_covered": "abc", "missing_days": [99, "x", 3],
    }]}
    s = normalise_extraction(raw, _TRIAGED, ThreadPayload())[0]
    assert s["month"] is None and s["year"] is None
    assert s["period_type"] == "unknown"
    assert s["days_covered"] == 0
    assert s["missing_days"] == [3]


def test_sheet_printed_name_overrides_the_triaged_one():
    """Pass 2 was looking straight at the header."""
    raw = {"sheets": [{"source": "sheet.pdf", "employee_name": "Someone Else"}]}
    s = normalise_extraction(raw, _TRIAGED, ThreadPayload())[0]
    assert s["employee_name"] == "Someone Else"


def test_extraction_falls_back_to_the_triaged_identity():
    """A sheet whose header pass 2 could not read still keeps pass 1's answer
    rather than losing the employee entirely."""
    raw = {"sheets": [{"source": "sheet.pdf", "employee_name": None}]}
    s = normalise_extraction(raw, _TRIAGED, ThreadPayload())[0]
    assert s["employee_name"] == "Bhargavi Prabhu"
    assert s["employee_id"] == "E2506943"
