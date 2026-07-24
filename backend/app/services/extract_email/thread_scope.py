"""When Extract Email should include the prior thread message."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage

_DOC_CONTENT_TYPES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.ms-excel",
    "message/rfc822",
})
_DOC_EXTS = (".pdf", ".docx", ".xlsx", ".doc", ".xls", ".eml")
_GENERIC_INLINE_RE = re.compile(
    r"^(image\d{2,3}\.(png|jpe?g|gif)|outlook-.+\.(png|jpe?g|gif|bmp)"
    r"|c2_signature_.+\.(png|jpe?g|gif))$", re.I)


def _is_image(a: dict) -> bool:
    return (a.get("content_type") or "").lower().startswith("image/")


def _is_doc(a: dict) -> bool:
    ct = (a.get("content_type") or "").lower()
    fn = (a.get("filename") or "").lower()
    if ct.startswith("image/"):
        return False
    return ct in _DOC_CONTENT_TYPES or fn.endswith(_DOC_EXTS)


def _is_body_junk_image(a: dict, body_html: str | None, *, min_img_bytes: int) -> bool:
    if not _is_image(a):
        return False
    size = a.get("size")
    if isinstance(size, (int, float)) and 0 < size < min_img_bytes:
        return True
    if a.get("is_inline") is True:
        return True
    cid = (a.get("cid") or "").strip().strip("<>")
    if cid and body_html and f"cid:{cid}" in body_html:
        return True
    return bool(_GENERIC_INLINE_RE.match(a.get("filename") or ""))


def has_extractable_attachments(email: EmailMessage) -> bool:
    """True when this message carries document/screenshot attachments to analyse.

    Lightweight replies ("Approved.", sick-leave cover notes WITH pdfs) must
    NOT trigger a merge of the prior thread message's attachments."""
    from app.core.config import settings

    min_img = settings.min_image_attachment_kb * 1024
    body_html = getattr(email, "body_html", None)
    for a in email.attachments or []:
        if not isinstance(a, dict):
            continue
        if _is_doc(a):
            return True
        if _is_image(a) and not _is_body_junk_image(a, body_html, min_img_bytes=min_img):
            return True
    return False


async def prior_message_for_merge(
    db: AsyncSession, row: EmailMessage,
) -> EmailMessage | None:
    """Prior message in the same conversation — only for approval-only replies.

    When the selected email already has PDFs/DOCX/etc., extract THAT message
    alone. Merge the prior message only when this one has no real attachments
    (e.g. manager reply "Approved." with no re-sent timesheet)."""
    if not row.conversation_id or has_extractable_attachments(row):
        return None
    return (await db.execute(
        select(EmailMessage)
        .where(
            EmailMessage.conversation_id == row.conversation_id,
            EmailMessage.received_at < row.received_at,
        )
        .order_by(EmailMessage.received_at.desc())
        .limit(1)
    )).scalar_one_or_none()
