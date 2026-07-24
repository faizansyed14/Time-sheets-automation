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
import re
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


_DOC_EXTS = (".pdf", ".docx", ".xlsx", ".xls", ".doc", ".eml")
_DOC_CTS = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.ms-excel",
    "message/rfc822",
    "application/eml",
}


def _guess_content_type(filename: str, declared: str, raw: bytes) -> str:
    """Prefer real MIME from filename/magic — EML parts often say octet-stream."""
    name = (filename or "").lower()
    if raw.startswith(b"%PDF") or name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if name.endswith((".xlsx", ".xls")):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if name.endswith(".eml"):
        return "message/rfc822"
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
        if declared.startswith("image/"):
            return declared
        ext = name.rsplit(".", 1)[-1]
        return "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    if declared and declared.lower() not in ("application/octet-stream", "application/binary"):
        return declared
    return declared or "application/octet-stream"


def _is_document_part(filename: str, content_type: str) -> bool:
    fn = (filename or "").lower()
    ct = (content_type or "").lower()
    return fn.endswith(_DOC_EXTS) or ct in _DOC_CTS


def _nested_inner(part):
    """The parsed Message inside a message/rfc822 part, if any."""
    payload = part.get_payload()
    if isinstance(payload, list) and payload:
        return payload[0]
    return payload if hasattr(payload, "as_bytes") else None


def _nested_message_bytes(part) -> bytes:
    """Raw .eml bytes of a message/rfc822 part.

    ``get_payload(decode=True)`` returns None for these — the payload is an
    already-parsed Message, not an encoded string — which is why a forwarded
    email used to reach the preview with size 0 and no way to open it.
    """
    inner = _nested_inner(part)
    if inner is not None:
        try:
            return inner.as_bytes()
        except Exception:
            pass
    payload = part.get_payload()
    if isinstance(payload, str) and payload.strip():
        return payload.encode("utf-8", "replace")
    return part.get_payload(decode=True) or b""


def _nested_message_name(part) -> str:
    """Name a nested email after its own Subject, the way Outlook does."""
    inner = _nested_inner(part)
    subject = ""
    if inner is not None and hasattr(inner, "get"):
        subject = _decode_header_value(inner.get("Subject"))
    subject = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", subject).strip()
    return f"{subject[:120]}.eml" if subject else "Forwarded message.eml"


def _walk_parts(part, *, root: bool = True):
    """Like ``Message.walk()``, but a nested message/rfc822 is ONE leaf.

    The stdlib walk descends into forwarded mail, which hoisted the inner
    email's attachments into the outer email's list and left the forwarded
    message itself empty. Stopping at the boundary preserves the real
    structure: a nested email is an attachment you open to see its own body
    and its own files, at any depth.
    """
    yield part
    if not root and part.get_content_type() == "message/rfc822":
        return
    if part.is_multipart():
        for sub in part.get_payload():
            if hasattr(sub, "walk"):
                yield from _walk_parts(sub, root=False)


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
    inline_images: dict[str, dict[str, Any]] = {}
    attachments: list[dict[str, Any]] = []

    for part in _walk_parts(msg):
        ct = part.get_content_type()
        disp = str(part.get("Content-Disposition", "")).lower()
        cid = (part.get("Content-Id") or "").strip().strip("<>")
        is_attachment = "attachment" in disp
        is_multipart = ct.startswith("multipart/")
        fn = part.get_filename() or ""

        if is_multipart:
            continue

        # A forwarded/attached email — kept whole so it opens as its own
        # email (headers, body, its own attachments) instead of having its
        # parts spilled into this one.
        if ct == "message/rfc822" and part is not msg:
            nested = _nested_message_bytes(part)
            attachments.append({
                "filename": fn or _nested_message_name(part),
                "content_type": "message/rfc822",
                "size": len(nested),
                "data_b64": base64.b64encode(nested).decode(),
            })
            continue

        if ct == "text/plain" and not is_attachment and not body_text:
            body_text = _decode_text(part)
            continue
        if ct == "text/html" and not is_attachment and not body_html:
            body_html = _decode_text(part)
            continue

        # Signature/body CID images — inline into HTML when referenced.
        if cid and ct.startswith("image/") and not is_attachment and not _is_document_part(fn, ct):
            data = part.get_payload(decode=True) or b""
            b64 = base64.b64encode(data).decode()
            inline_images[cid] = {
                "content_type": ct,
                "data_b64": b64,
                "data_uri": f"data:{ct};base64,{b64}",
                "size": len(data),
                "filename": fn or f"inline_{cid}.png",
            }
            continue

        # Downloadable / previewable files: disposition=attachment, named
        # documents (PDF/DOCX/XLSX/EML), or named images that aren't CID body art.
        keep = (
            is_attachment
            or _is_document_part(fn, ct)
            or (bool(fn) and ct.startswith("image/") and not cid)
        )
        if not keep:
            continue

        raw_att = part.get_payload(decode=True) or b""
        attachments.append({
            "filename": fn or "attachment",
            "content_type": _guess_content_type(fn, ct, raw_att),
            "size": len(raw_att),
            "data_b64": base64.b64encode(raw_att).decode(),
        })

    if inline_images:
        from app.services.inbox.inline_images import (
            _CID_REF_RE,
            cid_ref_matches,
            strip_unresolved_cids,
        )

        parts = [
            {
                "cid": cid_val,
                "filename": info["filename"],
                "data_uri": info["data_uri"],
            }
            for cid_val, info in inline_images.items()
        ]

        def _uri_for_ref(ref: str) -> str | None:
            for p in parts:
                if cid_ref_matches(
                    ref, cid=p["cid"], filename=p["filename"],
                ):
                    return p["data_uri"]
            return None

        def _sub(m: re.Match) -> str:
            return _uri_for_ref(m.group(1)) or m.group(0)

        if body_html:
            body_html = _CID_REF_RE.sub(_sub, body_html)
            body_html = strip_unresolved_cids(body_html)

        # Unreferenced inline parts stay as downloadable attachments.
        html = body_html or ""
        for cid_val, info in inline_images.items():
            ref = f"cid:{cid_val}"
            if ref not in html and f"cid:{cid_val.split('@')[0]}" not in html:
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
