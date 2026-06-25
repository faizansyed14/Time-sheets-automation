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
    """An .eml timesheet alongside a stray inline image: the .eml is the
    timesheet and the image becomes the approval screenshot — the image must
    NOT be treated as the timesheet."""
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
    assert by_name["logo.png"] == "approval_screenshot"  # not "timesheet"
