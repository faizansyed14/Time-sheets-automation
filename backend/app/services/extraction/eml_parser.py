"""
EML file parser — RFC 2822 email files to a structured dict.

Uses Python's built-in ``email`` module; no extra dependencies.
Inline CID images are resolved to data URIs so the HTML body renders in a
sandboxed iframe without any additional network requests.
"""
from __future__ import annotations

import base64
import email
import email.header
import email.policy
from typing import Any


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    out = ""
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            out += fragment.decode(charset or "utf-8", errors="replace")
        else:
            out += fragment
    return out


def _decode_text(part) -> str:
    raw = part.get_payload(decode=True)
    if not raw:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def parse_eml(raw: bytes) -> dict[str, Any]:
    """Parse raw EML bytes → JSON-serialisable dict.

    Returns:
        subject, from_, to, date  – decoded header strings
        body_text                 – plaintext body (may be empty)
        body_html                 – HTML body with CID images inlined as data URIs
        attachments               – list of {filename, content_type, size} dicts
    """
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)

    subject = _decode_header_value(msg.get("Subject"))
    from_ = _decode_header_value(msg.get("From"))
    to = _decode_header_value(msg.get("To"))
    date = str(msg.get("Date", ""))

    body_text = ""
    body_html = ""
    inline_images: dict[str, str] = {}   # content-id → data URI
    attachments: list[dict[str, Any]] = []

    for part in msg.walk():
        ct = part.get_content_type()
        disp = str(part.get("Content-Disposition", "")).lower()
        cid = (part.get("Content-Id") or "").strip().strip("<>")
        is_attachment = "attachment" in disp
        is_multipart = ct.startswith("multipart/")

        if is_multipart:
            continue

        if ct == "text/plain" and not is_attachment and not body_text:
            body_text = _decode_text(part)
        elif ct == "text/html" and not is_attachment and not body_html:
            body_html = _decode_text(part)
        elif cid and ct.startswith("image/"):
            # Inline image (Content-Id present) — embed as data URI.
            data = part.get_payload(decode=True) or b""
            b64 = base64.b64encode(data).decode()
            inline_images[cid] = f"data:{ct};base64,{b64}"
        elif is_attachment:
            fn = part.get_filename() or "attachment"
            raw_att = part.get_payload(decode=True) or b""
            attachments.append({
                "filename": fn,
                "content_type": ct,
                "size": len(raw_att),
                "data_b64": base64.b64encode(raw_att).decode(),
            })

    # Replace cid: references in the HTML body so it renders self-contained.
    if body_html and inline_images:
        for cid_val, data_uri in inline_images.items():
            body_html = body_html.replace(f"cid:{cid_val}", data_uri)

    return {
        "subject": subject,
        "from_": from_,
        "to": to,
        "date": date,
        "body_text": body_text,
        "body_html": body_html,
        "attachments": attachments,
    }
