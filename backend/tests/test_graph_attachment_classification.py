"""
Microsoft Graph attachment classification — a forwarded email that carries a
timesheet PDF must be treated as a TIMESHEET (so its nested PDF is extracted),
never as a flat image / manager-approval screenshot.
"""
from app.services.email_provider.graph_provider import _build, _classify


def test_eml_attachment_classified_as_timesheet():
    # A forwarded email attached as a .eml file (contentType may be octet-stream).
    assert _classify("Sri Naachammai.eml", "application/octet-stream", has_doc=False) == "timesheet"
    assert _classify("forwarded.eml", "message/rfc822", has_doc=True) == "timesheet"


def test_eml_is_never_approval_even_with_keyword():
    # Even an approval-sounding name on an .eml is still the timesheet container.
    assert _classify("manager approval.eml", "message/rfc822", has_doc=False) == "timesheet"


def test_item_attachment_forwarded_email_is_timesheet():
    msg = {
        "id": "m1",
        "from": {"emailAddress": {"name": "Client", "address": "c@x.com"}},
        "subject": "FWD: TIMESHEET May 2026",
        "attachments": [
            {"@odata.type": "#microsoft.graph.itemAttachment", "id": "a1",
             "name": "TIMESHEET for May 2026", "size": 400000},
        ],
    }
    built = _build(msg)
    kinds = [(a.filename, a.kind, a.content_type) for a in built.attachments]
    assert len(kinds) == 1
    fn, kind, ct = kinds[0]
    assert kind == "timesheet"
    assert fn.endswith(".eml")
    assert ct == "message/rfc822"


def test_eml_file_plus_inline_image_does_not_hijack_as_timesheet():
    """An .eml timesheet alongside a stray inline logo: the .eml is the
    timesheet, and Graph's own isInline=True on the logo means it's a
    signature/banner image, not a real approval screenshot — it must be
    "other", never "timesheet" and never "approval_screenshot"."""
    msg = {
        "id": "m2",
        "from": {"emailAddress": {"name": "Client", "address": "c@x.com"}},
        "subject": "FWD timesheet",
        "attachments": [
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "f1",
             "name": "Sri.eml", "contentType": "message/rfc822", "size": 420000},
            {"@odata.type": "#microsoft.graph.fileAttachment", "id": "f2",
             "name": "logo.png", "contentType": "image/png", "size": 40000,
             "isInline": True},
        ],
    }
    built = _build(msg)
    by_name = {a.filename: a.kind for a in built.attachments}
    assert by_name["Sri.eml"] == "timesheet"
    assert by_name["logo.png"] == "other"  # signature/banner, not the timesheet, not a screenshot


def test_is_inline_flag_beats_filename_for_a_real_looking_name():
    """isInline=True is authoritative even when the filename looks like a
    real screenshot — Graph itself says this image lives in the body."""
    assert _classify("Screenshot 2026-07-07 at 2.37.06 PM.png", "image/png",
                     has_doc=True, is_inline=True) == "other"


def test_real_screenshot_without_is_inline_is_still_approval_screenshot():
    """A genuine screenshot attachment (isInline False/absent) alongside a
    real doc is still classified as the approval screenshot."""
    assert _classify("Screenshot 2026-07-07 at 2.37.06 PM.png", "image/png",
                     has_doc=True, is_inline=False) == "approval_screenshot"


def test_generic_body_image_name_is_junk_even_without_is_inline_flag():
    """Providers/rows that predate the isInline flag fall back to the
    filename pattern — image00N / Outlook- / C2_signature_ names are junk."""
    assert _classify("image003.png", "image/png", has_doc=True, is_inline=False) == "other"
    assert _classify("Outlook-Signature .png", "image/png", has_doc=True, is_inline=False) == "other"
    assert _classify(
        "C2_signature_facebook2_8163aee3-593b-481f-aecf-3f004bf0d8bf.png",
        "image/png", has_doc=True, is_inline=False) == "other"


def test_tiny_image_is_junk_but_documents_of_any_size_are_not():
    """Images under MIN_IMAGE_ATTACHMENT_KB are logos/icons → 'other', even
    with a real-looking name. The size rule must NEVER touch documents."""
    tiny = 10 * 1024   # 10 KB
    big = 200 * 1024   # 200 KB
    assert _classify("Screenshot 2026-07-07.png", "image/png",
                     has_doc=True, size=tiny) == "other"
    assert _classify("Screenshot 2026-07-07.png", "image/png",
                     has_doc=True, size=big) == "approval_screenshot"
    # A small PDF/DOCX is still a timesheet — size only applies to images.
    assert _classify("sheet.pdf", "application/pdf", has_doc=False, size=tiny) == "timesheet"
    # size=0 (unknown) must not trigger the rule.
    assert _classify("Screenshot real.png", "image/png", has_doc=True, size=0) == "approval_screenshot"
