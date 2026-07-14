"""Full .eml export — reconstruction includes body + every attachment."""
from app.models.email_message import EmailMessage
from app.services.email_provider import get_email_provider
from app.services.extraction.eml_parser import parse_eml
from app.services.inbox.eml_export import build_full_eml, eml_filename


def test_eml_filename_sanitised():
    assert eml_filename('RE: TIMESHEET / May? <x>') .endswith(".eml")
    assert "/" not in eml_filename("a/b")


async def test_build_full_eml_contains_everything():
    provider = get_email_provider()
    msg = await provider.get_message("MSG-0001")
    assert msg is not None
    row = EmailMessage(
        provider_message_id="MSG-0001",
        sender_name=msg.sender_name, sender_email=msg.sender_email,
        subject=msg.subject, received_at=msg.received_at,
        body_text=msg.body_text, body_html=msg.body_html,
        attachments=[{
            "attachment_id": a.attachment_id, "filename": a.filename,
            "content_type": a.content_type, "size": a.size,
            "kind": a.kind, "cid": a.cid,
        } for a in msg.attachments],
    )
    data, fname = await build_full_eml(provider, row)
    assert fname.endswith(".eml") and data

    # Parse it back: subject survives and EVERY attachment is inside.
    # Inline signature/logo images keep Content-ID; timesheet/approval files
    # are exported as explicit attachments (even when the source had a CID).
    parsed = parse_eml(data)
    assert (msg.subject or "") in (parsed.get("subject") or "")
    names = {a.get("filename") for a in parsed.get("attachments", [])}
    from email import message_from_bytes, policy
    mime = message_from_bytes(data, policy=policy.SMTP)
    inline_cids = {(p.get("Content-Id") or "").strip("<>")
                   for p in mime.walk() if p.get("Content-Id")}
    for a in msg.attachments:
        if a.kind in ("timesheet", "approval_screenshot"):
            assert a.filename in names, f"document missing from eml: {a.filename}"
        elif a.cid:
            assert a.cid.strip("<>") in inline_cids, \
                f"inline image missing from eml: {a.filename} (cid {a.cid})"
        else:
            assert a.filename in names, f"attachment missing from eml: {a.filename}"


def test_eml_collect_keeps_outlook_style_pdf_with_content_id():
    """Graph/Outlook often tag real PDF attachments with Content-Id — must not skip."""
    from email import policy
    from email.message import EmailMessage

    from app.services.extraction import file_processor as fp
    from tests.test_eml_storage_and_provenance import _timesheet_pdf

    pdf = _timesheet_pdf("Muhammad Aamir", "E2206251", "June 2026")
    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = "TIMESHEET for June"
    msg.set_content("Please find attached attendance report")
    part = msg.add_attachment(
        pdf, maintype="application", subtype="pdf",
        filename="SGRP_SmartTime_Attendance_Report.PDF",
    )
    part.add_header("Content-Id", "<19f4a826b7f7b94dd5b1>")
    part.replace_header(
        "Content-Disposition",
        'inline; filename="SGRP_SmartTime_Attendance_Report.PDF"',
    )

    atts = fp.eml_all_attachments(msg.as_bytes())
    assert len(atts) == 1, atts
    assert atts[0][2] == "pdf"
    assert "Attendance" in atts[0][0]


def test_eml_collect_keeps_approval_screenshot_with_content_id():
    """Approval PNGs referenced by CID must still be analysed by Extract Email."""
    from email import policy
    from email.message import EmailMessage

    from app.services.extraction import file_processor as fp

    payload = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 60_000)
    msg = EmailMessage(policy=policy.SMTP)
    msg.set_content("Approval attached")
    part = msg.add_attachment(
        payload, maintype="image", subtype="png",
        filename="Screenshot 2026-07-08 at 12.03.40 PM.png",
    )
    part.add_header("Content-Id", "<19f4a8385f7fef9ca334>")
    part.replace_header("Content-Disposition", 'inline; filename="Screenshot.png"')

    atts = fp.eml_all_attachments(msg.as_bytes())
    assert len(atts) == 1, atts
    assert atts[0][2] == "image"
