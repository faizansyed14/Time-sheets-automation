"""PII redaction — mailbox/secrets masked; timesheet identity targets never are.

Product policy:
- Emp name / ID / dates on sheets stay byte-identical.
- Emails, phones, Password: lines, From/To headers, signature footers go.
"""
from app.core.pii import (
    HEADER_VALUE_TOKEN,
    PHONE_TOKEN,
    SECRET_TOKEN,
    SIGNATURE_NOTE,
    THREAD_QUOTE_NOTE,
    assert_no_plaintext_pii,
    pseudonymize_email,
    scrub_email_for_llm,
    scrub_text,
)


def test_email_addresses_become_stable_pseudonyms():
    out = scrub_text("Contact Kevin Dsouza at kevin.dsouza@acme.com please")
    assert "kevin.dsouza@acme.com" not in out
    assert "Kevin Dsouza" in out  # display name kept — it grounds identity
    assert pseudonymize_email("kevin.dsouza@acme.com") in out
    assert pseudonymize_email("KEVIN.DSOUZA@ACME.COM") == pseudonymize_email("kevin.dsouza@acme.com")
    assert pseudonymize_email("someone.else@acme.com") != pseudonymize_email("kevin.dsouza@acme.com")
    header = scrub_text("From: Sylvia Noronha <sylvia@client.ae>")
    assert "sylvia@client.ae" not in header
    assert HEADER_VALUE_TOKEN in header


def test_quoted_forward_headers_lose_addresses():
    body = ("From: Sylvia Noronha <sylvia@client.ae>\n"
            "To: timesheets@adr.ae; hr@adr.ae\n"
            "Subject: Approved\n\nApproved. Please process.")
    out = scrub_text(body)
    for addr in ("sylvia@client.ae", "timesheets@adr.ae", "hr@adr.ae"):
        assert addr not in out
    assert "Approved. Please process." in out
    assert HEADER_VALUE_TOKEN in out


def test_password_in_signature_is_secret_redacted():
    body = (
        "Please find my timesheet attached.\n\n"
        "Thanks\n"
        "John Smith\n"
        "john.smith@acme.com\n"
        "Password: Summer2026!\n"
        "Mobile: 050 123 4567\n"
    )
    subj, scrubbed = scrub_email_for_llm("TIMESHEET June 2026", body)
    assert "Summer2026!" not in scrubbed
    assert "john.smith@acme.com" not in scrubbed
    assert "050 123 4567" not in scrubbed
    assert SIGNATURE_NOTE.strip() not in scrubbed
    assert "Thanks" in scrubbed
    assert "John Smith" in scrubbed
    assert "Please find my timesheet attached." in scrubbed
    assert "June 2026" in subj
    # Secrets mid-body (not only in signature) are still tokenised.
    mid = scrub_text("VPN password: Winter99 for access")
    assert "Winter99" not in mid and SECRET_TOKEN in mid


def test_signature_cut_keeps_short_approval():
    body = "Approved. Thanks"
    _, out = scrub_email_for_llm("Re: sheet", body)
    assert "Approved" in out


def test_international_and_labelled_phones_masked():
    assert "+971" not in scrub_text("call me on +971 50 123 4567 anytime")
    assert PHONE_TOKEN in scrub_text("Mobile: 050 123 4567")
    assert PHONE_TOKEN in scrub_text("Tel. (04) 123-4567")
    # DHRE Outlook signature shorthand + zero-width junk
    assert "+971" not in scrub_text("T: +971 4 777 8526\u200b\nM: 050000000")
    assert scrub_text("T: +971 4 777 8526").count(PHONE_TOKEN) >= 1
    assert PHONE_TOKEN in scrub_text("M: 050000000")
    # Clock suffixes must stay (do not treat AM/PM as phone labels)
    assert "8:30 AM" in scrub_text("1-Jun-26\n8:30 AM\n5:30 PM\n9")


def test_sheet_signature_footer_scrubbed_not_cut():
    body = (
        "ATTENDANCE SHEET\nEMP NO: E2406601\n"
        "1-Jun-26 8:30 AM 5:30 PM 9\n\n"
        "Sri Lalitha\nSumita Uppal\n"
        "EMPLOYEE SIGNATURE\nMANAGER SIGNATURE\n\n"
        "Sri Lalitha Raghava\nSalesforce Developer\n"
        "T: +971 4 777 8526\nM: 050000000\n"
        "DUBAI HOLDING REAL ESTATE\n"
    )
    _, scrubbed = scrub_email_for_llm("TIMESHEET June 2026", body)
    assert "ATTENDANCE SHEET" in scrubbed
    assert "EMPLOYEE SIGNATURE" in scrubbed
    assert "DUBAI HOLDING" in scrubbed
    assert SIGNATURE_NOTE.strip() not in scrubbed
    assert "+971" not in scrubbed
    assert "050000000" not in scrubbed


def test_quoted_reply_thread_scrubbed_not_cut():
    """Whole thread kept; PII inside signatures and quoted history tokenised."""
    from app.core.pii import scrub_email_for_llm

    body = (
        "Dear Maria , team ,\n\n"
        "Kindly find the time sheet in attachment with approval\n\n"
        "Best regards,\n"
        "Mohamed Badr Hassan Mohamed\n"
        "Email: mb.mohamed@adnic.ae\n\n"
        "From: Maria Lourdes <Des@alpha.ae>\n"
        "Sent: 14 July 2026 9:54 AM\n"
        "To: Mohamed Badr <mb.mohamed@adnic.ae>; timesheet@alpha.ae\n"
        "Cc: Zainab Khan <zainab.khan@alpha.ae>\n"
        "Subject: RE: June Timesheet\n\n"
        "Hi Badr,\nPlease print your timesheet.\n"
        "DID: +971 2 2058-679\n"
        "Moblie: +971-56-9241659\n"
    )
    _, scrubbed = scrub_email_for_llm("RE: June Timesheet", body)
    assert "Kindly find the time sheet in attachment" in scrubbed
    assert "Please print your timesheet." in scrubbed
    assert HEADER_VALUE_TOKEN in scrubbed
    assert "zainab.khan@alpha.ae" not in scrubbed
    assert "mb.mohamed@adnic.ae" not in scrubbed
    assert "+971" not in scrubbed
    assert THREAD_QUOTE_NOTE.strip() not in scrubbed
    assert SIGNATURE_NOTE.strip() not in scrubbed


def test_legacy_cut_modes_still_available():
    body_sig = (
        "Please find my timesheet attached.\n\n"
        "Thanks\n"
        "john.smith@acme.com\n"
    )
    _, cut_sig = scrub_email_for_llm("S", body_sig, cut_signature=True)
    assert SIGNATURE_NOTE.strip() in cut_sig
    assert "john.smith@acme.com" not in cut_sig

    body_thread = (
        "Latest note only — please see below.\n\n"
        "From: Other <other@x.com>\n"
        "Sent: yesterday\n"
        "Prior message body\n"
    )
    _, cut_thread = scrub_email_for_llm("S", body_thread, cut_quoted_thread=True)
    assert THREAD_QUOTE_NOTE.strip() in cut_thread
    assert "Prior message body" not in cut_thread


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
    line = "tel 05-06-2026 meeting"
    assert "05-06-2026" in scrub_text(line)


def test_none_and_disabled_passthrough(monkeypatch):
    assert scrub_text(None) == ""
    assert scrub_text("") == ""
    from app.core import pii
    monkeypatch.setattr(pii.settings, "pii_redaction", False)
    s = "kevin@acme.com +971501234567 Password: x"
    assert scrub_text(s) == s
    assert scrub_email_for_llm("S", "Thanks\nPassword: x") == ("S", "Thanks\nPassword: x")


def test_extract_prompt_has_no_sender_address():
    from app.models.email_message import EmailMessage
    from app.services.agents import full_email_extract as fx

    mail = EmailMessage(
        provider_message_id="X", sender_name="Kevin Dsouza",
        sender_email="kevin.dsouza@acme.com",
        subject="TIMESHEET June 2026 | Kevin Dsouza | E2507067",
        body_text="", attachments=[])
    unit = fx.SheetUnit(name="sheet.pdf", ftype="pdf", payload=b"", images=[b"x"], text="")
    prompt = fx._extract_prompt(mail, unit)
    assert "kevin.dsouza@acme.com" not in prompt
    assert "EMAIL FROM" not in prompt
    assert "E2507067" in prompt and "June 2026" in prompt


def test_assert_no_plaintext_pii_helper():
    clean = scrub_text("hi leak@example.com Password: Secret99")
    assert assert_no_plaintext_pii(clean, canaries=["leak@example.com", "Secret99"]) == []
