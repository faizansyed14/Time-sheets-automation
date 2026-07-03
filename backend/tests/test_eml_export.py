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
    # Real documents appear as attachments; inline signature/logo images keep
    # their Content-ID + inline disposition exactly like the original message
    # (so they render in the HTML body and are never mistaken for documents).
    parsed = parse_eml(data)
    assert (msg.subject or "") in (parsed.get("subject") or "")
    names = {a.get("filename") for a in parsed.get("attachments", [])}
    from email import message_from_bytes, policy
    mime = message_from_bytes(data, policy=policy.SMTP)
    inline_cids = {(p.get("Content-Id") or "").strip("<>")
                   for p in mime.walk() if p.get("Content-Id")}
    for a in msg.attachments:
        if a.cid:
            assert a.cid.strip("<>") in inline_cids, \
                f"inline image missing from eml: {a.filename} (cid {a.cid})"
        else:
            assert a.filename in names, f"attachment missing from eml: {a.filename}"
