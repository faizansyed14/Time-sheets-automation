"""Upload -> the two-pass thread reader.

Upload now sends every submission through the SAME reader Extract Email uses
(app.services.extract_email.thread_extract.collect_thread_payload), instead of
classifying and extracting each sheet separately. A real .eml upload already
carries everything that reader needs; a bare file (PDF/XLSX/DOCX/image, no
email envelope) has to be wrapped as a minimal one-attachment message first —
these tests pin that bridge, with no LLM call involved: collect_thread_payload
is a pure, deterministic parser.
"""
from email.message import EmailMessage as MimeMessage

from app.services.extract_email.thread_extract import collect_thread_payload
from app.services.extract_email.upload import as_thread_messages


def test_bare_pdf_is_wrapped_and_recovered_byte_for_byte():
    original = b"%PDF-1.4 fake sheet contents"
    messages = as_thread_messages("sheet.pdf", original)
    assert [label for label, _ in messages] == ["sheet.pdf"]

    payload = collect_thread_payload(messages)
    assert [(n, b, f) for n, b, f in payload.files] == [("sheet.pdf", original, "pdf")]
    assert payload.images == []


def test_bare_image_is_wrapped_and_recognised_as_an_image():
    original = b"\x89PNG\r\n\x1a\n" + b"0" * 40_000
    messages = as_thread_messages("screenshot.png", original)

    payload = collect_thread_payload(messages)
    assert payload.files == []
    assert [(n, b) for n, b in payload.images] == [("screenshot.png", original)]


def test_an_uploaded_eml_passes_through_unwrapped():
    inner = MimeMessage()
    inner["Subject"] = "TIMESHEET June 2026"
    inner["From"] = "employee@alpha.ae"
    inner.set_content("See attached.")
    inner.add_attachment(b"%PDF-1.4 real sheet", maintype="application",
                         subtype="pdf", filename="timesheet.pdf")
    eml_bytes = inner.as_bytes()

    messages = as_thread_messages("thread.eml", eml_bytes)
    # Not re-wrapped — the exact same bytes go straight through.
    assert messages == [("thread.eml", eml_bytes)]

    payload = collect_thread_payload(messages)
    assert [n for n, _, _ in payload.files] == ["timesheet.pdf"]
    assert "TIMESHEET June 2026" in payload.bodies


def test_wrapped_upload_has_no_stray_body_text():
    """The synthetic envelope carries no note of its own — only the file."""
    messages = as_thread_messages("sheet.xlsx", b"PK\x03\x04 fake xlsx")
    payload = collect_thread_payload(messages)
    assert "sheet.xlsx" in payload.bodies  # the manifest header line only
    assert payload.files[0][2] == "xlsx"
