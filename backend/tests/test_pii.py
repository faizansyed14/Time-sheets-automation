"""PII redaction — addresses/phones are masked, extraction targets never are.

The guarantees the extraction accuracy rests on:
- email addresses become STABLE pseudonyms (thread-following still works),
- only unambiguous phone numbers are masked (international "+..." format or
  behind a phone label) — bare digit runs, employee IDs, dates and clock
  times are byte-identical after scrubbing,
- the vision batch prompt carries no sender address at all.
"""
from app.core.pii import PHONE_TOKEN, pseudonymize_email, scrub_text


def test_email_addresses_become_stable_pseudonyms():
    out = scrub_text("From: Kevin Dsouza <kevin.dsouza@acme.com>")
    assert "kevin.dsouza@acme.com" not in out
    assert "Kevin Dsouza" in out  # display name kept — it grounds identity
    assert pseudonymize_email("kevin.dsouza@acme.com") in out
    # Same address (any casing) -> same token, so the model can follow a thread.
    assert pseudonymize_email("KEVIN.DSOUZA@ACME.COM") == pseudonymize_email("kevin.dsouza@acme.com")
    assert pseudonymize_email("someone.else@acme.com") != pseudonymize_email("kevin.dsouza@acme.com")


def test_quoted_forward_headers_lose_addresses_only():
    body = ("From: Sylvia Noronha <sylvia@client.ae>\n"
            "To: timesheets@adr.ae; hr@adr.ae\n"
            "Subject: Approved\n\nApproved. Please process.")
    out = scrub_text(body)
    for addr in ("sylvia@client.ae", "timesheets@adr.ae", "hr@adr.ae"):
        assert addr not in out
    assert "Sylvia Noronha" in out and "Approved. Please process." in out


def test_international_and_labelled_phones_masked():
    assert "+971" not in scrub_text("call me on +971 50 123 4567 anytime")
    assert PHONE_TOKEN in scrub_text("Mobile: 050 123 4567")
    assert PHONE_TOKEN in scrub_text("Tel. (04) 123-4567")
    assert PHONE_TOKEN in scrub_text("WhatsApp +14155552671")


def test_extraction_targets_are_byte_identical():
    text = ("ATTENDANCE SHEET\n"
            "Employee Name: Kevin Dsouza\n"
            "Emp No: E2507067\n"
            "01 June 26  9:00 am  6:00 pm  8\n"
            "2026-06-15  Public Holiday\n"
            "15/06/2026 annual leave, also written 15-06-2026\n"
            "Total working hours: 168")
    assert scrub_text(text) == text


def test_date_behind_phone_label_is_not_eaten():
    # A pathological line — the date must survive even behind a label word.
    line = "tel 05-06-2026 meeting"
    assert "05-06-2026" in scrub_text(line)


def test_none_and_disabled_passthrough(monkeypatch):
    assert scrub_text(None) == ""
    assert scrub_text("") == ""
    from app.core import pii
    monkeypatch.setattr(pii.settings, "pii_redaction", False)
    s = "kevin@acme.com +971501234567"
    assert scrub_text(s) == s


def test_batch_prompt_has_no_sender_address():
    """The Extract Email prompt must not carry the sender line; the subject
    (which often names the employee and period) must survive."""
    from app.models.email_message import EmailMessage
    from app.services.agents import full_email_extract as fx

    mail = EmailMessage(
        provider_message_id="X", sender_name="Kevin Dsouza",
        sender_email="kevin.dsouza@acme.com",
        subject="TIMESHEET June 2026 | Kevin Dsouza | E2507067",
        body_text="", attachments=[])
    unit = fx.SheetUnit(name="sheet.pdf", ftype="pdf", payload=b"", images=[b"x"], text="")
    prompt = fx._batch_prompt(mail, [unit])
    assert "kevin.dsouza@acme.com" not in prompt
    assert "EMAIL FROM" not in prompt
    assert "E2507067" in prompt and "June 2026" in prompt
