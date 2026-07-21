from email.message import EmailMessage
from email import policy

from app.services.extraction.eml_parser import parse_eml


def test_parse_eml_includes_image_attachment_even_if_inline_disposition():
    # Provider-like case: has filename + image content, but disposition is
    # not "attachment" (e.g. "inline" or missing). Vault preview should still
    # show it as an attachment chip.
    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = "TIMESHEET"
    msg.set_content("See attached.")

    payload = b"\xFF\xD8\xFF" + (b"\x00" * 50_000)  # looks like a JPEG header
    msg.add_attachment(payload, maintype="image", subtype="jpeg", filename="sheet.jpg")

    # Force "inline" (non-attachment) to simulate the problematic MIME shape.
    part = next(msg.walk())
    for p in msg.walk():
        if p.get_filename() == "sheet.jpg":
            part = p
            break
    part.replace_header("Content-Disposition", 'inline; filename="sheet.jpg"')

    parsed = parse_eml(msg.as_bytes())
    names = {a.get("filename") for a in parsed.get("attachments", [])}
    assert "sheet.jpg" in names


def test_parse_eml_image_with_content_id_and_attachment_disposition_is_listed_as_attachment():
    # Some providers set Content-Id on the actual attachment. The vault UI
    # must show it as an attachment chip, not treat it as cid-inline.
    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = "TIMESHEET"
    msg.set_content("See attached.")

    payload = b"\xFF\xD8\xFF" + (b"\x00" * 50_000)
    msg.add_attachment(payload, maintype="image", subtype="jpeg", filename="sheet2.jpg")

    for p in msg.walk():
        if p.get_filename() == "sheet2.jpg":
            p.add_header("Content-Id", "<sheet2@x>")
            # Force attachment disposition explicitly.
            p.replace_header("Content-Disposition", 'attachment; filename="sheet2.jpg"')
            break

    parsed = parse_eml(msg.as_bytes())
    names = {a.get("filename") for a in parsed.get("attachments", [])}
    assert "sheet2.jpg" in names


def test_parse_eml_cid_image_not_referenced_in_html_becomes_attachment():
    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = "TIMESHEET"
    # HTML body DOES NOT reference the CID.
    msg.add_alternative("<html><body><p>No cid refs here.</p></body></html>", subtype="html")

    payload = b"\xFF\xD8\xFF" + (b"\x00" * 50_000)
    msg.add_attachment(payload, maintype="image", subtype="jpeg", filename="sheet3.jpg")

    for p in msg.walk():
        if p.get_filename() == "sheet3.jpg":
            p.add_header("Content-Id", "<sheet3@x>")
            p.replace_header("Content-Disposition", 'inline; filename="sheet3.jpg"')
            break

    parsed = parse_eml(msg.as_bytes())
    names = {a.get("filename") for a in parsed.get("attachments", [])}
    assert "sheet3.jpg" in names

