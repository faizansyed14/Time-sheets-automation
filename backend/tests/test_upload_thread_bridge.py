"""Upload -> the two-pass thread reader.

Upload sends every submission through the SAME reader Extract Email uses
(app.services.extract_email.thread_extract.collect_thread). A real .eml/.msg
upload already carries everything that reader needs; any other file (PDF,
XLSX, CSV, TXT, image, no email envelope) is wrapped as a minimal
one-attachment message first — these tests pin that bridge, with no LLM call
involved: collect_thread is a pure, deterministic parser.
"""
from email.message import EmailMessage as MimeMessage

from app.services.extract_email.thread_extract import collect_thread
from app.services.extract_email.upload import as_thread_messages


def test_bare_pdf_is_wrapped_and_recovered_byte_for_byte():
    original = b"%PDF-1.4 fake sheet contents"
    messages = as_thread_messages("sheet.pdf", original)
    assert [label for label, _ in messages] == ["sheet.pdf"]

    th = collect_thread(messages)
    pdf_items = [it for it in th.items if it.name == "sheet.pdf"]
    assert len(pdf_items) == 1


def test_bare_csv_is_wrapped_and_sent_as_native_text():
    original = b"date,status\n2026-06-01,present\n2026-06-02,sick\n"
    messages = as_thread_messages("data.csv", original)
    th = collect_thread(messages)
    item = next(it for it in th.items if it.name == "data.csv")
    assert "2026-06-01" in item.text
    assert item.send_mode == "native"


def test_bare_txt_is_wrapped_and_sent_as_native_text():
    original = b"June attendance notes, informal."
    messages = as_thread_messages("notes.txt", original)
    th = collect_thread(messages)
    item = next(it for it in th.items if it.name == "notes.txt")
    assert "informal" in item.text


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

    th = collect_thread(messages)
    assert any(it.name == "timesheet.pdf" for it in th.items)
    assert any("TIMESHEET June 2026" in m.subject for m in th.messages)


def test_wrapped_upload_has_no_stray_body_text():
    """The synthetic envelope carries no note of its own — only the file."""
    messages = as_thread_messages("sheet.xlsx", b"PK\x03\x04 fake xlsx")
    th = collect_thread(messages)
    assert not any(m.body.strip() for m in th.messages)
    assert any(it.name == "sheet.xlsx" for it in th.items)
