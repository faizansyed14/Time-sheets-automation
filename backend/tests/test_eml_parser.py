import pytest
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


def test_parse_eml_converts_msg_bytes_via_extract_msg_first(monkeypatch):
    """A real .msg is an OLE compound binary, not RFC822 text — parse_eml
    must convert it via msg_to_eml_bytes before handing anything to
    email.message_from_bytes. Building a genuine .msg binary isn't
    practical in a unit test (extract-msg only reads the format, it has no
    encoder), so the converter itself is mocked here — this pins the WIRING:
    filename=".msg" routes through the converter, its output is what
    actually gets parsed."""
    import app.services.extraction.eml_parser as eml_parser_module

    real_eml = EmailMessage(policy=policy.SMTP)
    real_eml["Subject"] = "Converted from MSG"
    real_eml.set_content("Body text.")
    converted_bytes = real_eml.as_bytes()

    def fake_msg_to_eml_bytes(payload: bytes) -> bytes | None:
        assert payload == b"fake ole compound bytes"
        return converted_bytes

    monkeypatch.setattr(
        "app.services.extract_email.thread_extract.msg_to_eml_bytes", fake_msg_to_eml_bytes
    )

    parsed = eml_parser_module.parse_eml(b"fake ole compound bytes", filename="Forwarded note.msg")
    assert parsed["subject"] == "Converted from MSG"


def test_parse_eml_raises_when_msg_conversion_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.extract_email.thread_extract.msg_to_eml_bytes", lambda payload: None
    )
    with pytest.raises(ValueError):
        parse_eml(b"not really a msg file", filename="corrupt.msg")


def test_parse_eml_never_touches_the_msg_converter_for_a_plain_eml(monkeypatch):
    def _boom(payload: bytes):
        raise AssertionError("a .eml filename must never route through the .msg converter")

    monkeypatch.setattr("app.services.extract_email.thread_extract.msg_to_eml_bytes", _boom)

    msg = EmailMessage(policy=policy.SMTP)
    msg["Subject"] = "Plain EML"
    msg.set_content("Body.")
    parsed = parse_eml(msg.as_bytes(), filename="ordinary.eml")
    assert parsed["subject"] == "Plain EML"

