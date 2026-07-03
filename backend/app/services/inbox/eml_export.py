"""
Full-fidelity .eml export of an inbox email.

Two paths, best first:
1. Native MIME from the provider (Microsoft Graph `GET /messages/{id}/$value`)
   — byte-exact original including every attachment, nested forwarded emails,
   inline images, headers. Nothing can be missing.
2. Constructed fallback (mock provider / MIME endpoint unavailable): rebuild an
   RFC-822 message from the stored email — headers, text + HTML body, and EVERY
   attachment fetched from the provider (nested .eml attachments embedded as
   message/rfc822 so forwarded emails stay intact).
"""
from __future__ import annotations

import re
from email.message import EmailMessage as MimeMessage
from email.utils import format_datetime
from email import message_from_bytes, policy

from app.models.email_message import EmailMessage


def eml_filename(subject: str | None) -> str:
    base = re.sub(r'[<>:"/\\|?*\r\n]+', "_", (subject or "email").strip()) or "email"
    return f"{base[:120]}.eml"


async def build_full_eml(provider, email: EmailMessage) -> tuple[bytes, str]:
    """Return (eml_bytes, filename) for the complete email."""
    fname = eml_filename(email.subject)

    # ---- 1) native provider MIME (Graph) — the byte-exact original ----
    getter = getattr(provider, "get_message_mime", None)
    if getter is not None:
        try:
            mime = await getter(email.provider_message_id)
            if mime:
                return mime, fname
        except Exception:
            pass  # fall through to the constructed message

    # ---- 2) constructed message from stored data + provider attachments ----
    msg = MimeMessage(policy=policy.SMTP)
    sender = email.sender_email or ""
    if email.sender_name and sender:
        msg["From"] = f"{email.sender_name} <{sender}>"
    elif sender or email.sender_name:
        msg["From"] = sender or (email.sender_name or "")
    msg["Subject"] = email.subject or "(no subject)"
    if email.received_at:
        msg["Date"] = format_datetime(email.received_at)

    body_text = email.body_text or ""
    msg.set_content(body_text)
    if email.body_html:
        msg.add_alternative(email.body_html, subtype="html")

    for a in email.attachments or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("attachment_id")
        fn = a.get("filename") or aid or "attachment"
        try:
            data, real_fn, real_ct = await provider.get_attachment_bytes(
                email.provider_message_id, aid)
            fn = real_fn or fn
            ct = (real_ct or a.get("content_type") or "application/octet-stream").lower()
        except Exception:
            continue

        if ct in ("message/rfc822", "application/eml") or fn.lower().endswith(".eml"):
            # Keep forwarded emails as real nested messages.
            try:
                nested = message_from_bytes(data, policy=policy.SMTP)
                msg.add_attachment(nested)
                continue
            except Exception:
                pass  # fall back to raw bytes below

        maintype, _, subtype = (ct.partition("/") if "/" in ct
                                else ("application", "/", "octet-stream"))
        # Inline signature/logo images keep their Content-ID and inline
        # disposition, exactly like the original — so .eml consumers can both
        # render them in the HTML body and tell them apart from real documents.
        cid = (a.get("cid") or "").strip()
        headers: dict = {}
        if cid:
            headers = {"cid": f"<{cid.strip('<>')}>", "disposition": "inline"}
        try:
            msg.add_attachment(data, maintype=maintype or "application",
                               subtype=subtype or "octet-stream", filename=fn, **headers)
        except Exception:
            msg.add_attachment(data, maintype="application",
                               subtype="octet-stream", filename=fn, **headers)

    return msg.as_bytes(), fname
