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
    # content-id → data + metadata (sometimes providers set Content-Id on real
    # attachments; we only treat it as inline if HTML actually references it)
    inline_images: dict[str, dict[str, Any]] = {}
    attachments: list[dict[str, Any]] = []

    for part in msg.walk():
        ct = part.get_content_type()
        disp = str(part.get("Content-Disposition", "")).lower()
        cid = (part.get("Content-Id") or "").strip().strip("<>")
        is_attachment = "attachment" in disp
        is_multipart = ct.startswith("multipart/")
        fn = part.get_filename() or ""

        if is_multipart:
            continue

        if ct == "text/plain" and not is_attachment and not body_text:
            body_text = _decode_text(part)
        elif ct == "text/html" and not is_attachment and not body_html:
            body_html = _decode_text(part)
        # Content-Id images are usually inline (cid:...) but some providers
        # also set Content-Id on real attachments. If disposition is
        # attachment, keep it as an attachment so the UI can show it in
        # "Attachments (N)".
        elif cid and ct.startswith("image/") and not is_attachment:
            # Inline image (Content-Id present) — embed as data URI.
            data = part.get_payload(decode=True) or b""
            b64 = base64.b64encode(data).decode()
            inline_images[cid] = {
                "content_type": ct,
                "data_b64": b64,
                "data_uri": f"data:{ct};base64,{b64}",
                "size": len(data),
                "filename": fn or f"inline_{cid}.png",
            }
        # Some providers export real document images with a filename but without a
        # reliable `Content-Disposition: attachment` value (sometimes `inline`,
        # sometimes empty). For vault preview, prefer the filename + content-type.
        elif is_attachment or (fn and not cid and ct.startswith("image/")):
            raw_att = part.get_payload(decode=True) or b""
            attachments.append({
                "filename": fn,
                "content_type": ct,
                "size": len(raw_att),
                "data_b64": base64.b64encode(raw_att).decode(),
            })

    # Replace cid: references in the HTML body so it renders self-contained.
    # If provider set Content-Id but HTML does not reference it, treat as a
    # normal attachment so the UI still shows it.
    if inline_images:
        html = body_html or ""
        for cid_val, info in inline_images.items():
            ref = f"cid:{cid_val}"
            if html and ref in html:
                body_html = (body_html or "").replace(ref, info["data_uri"])
            else:
                # CID existed but HTML never used it => must be a real
                # attachment mislabeled as inline.
                attachments.append({
                    "filename": info["filename"],
                    "content_type": info["content_type"],
                    "size": info["size"],
                    "data_b64": info["data_b64"],
                })

    return {
        "subject": subject,
        "from_": from_,
        "to": to,
        "date": date,
        "body_text": body_text,
        "body_html": body_html,
        "attachments": attachments,
    }
