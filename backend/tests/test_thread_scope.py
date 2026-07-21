"""Tests for narrow thread merge scope (approval-only replies)."""
from __future__ import annotations

from app.models.email_message import EmailMessage
from app.services.extract_email.thread_scope import has_extractable_attachments


def _msg(**kw) -> EmailMessage:
    return EmailMessage(
        provider_message_id=kw.get("provider_message_id", "MSG-1"),
        sender_email="a@b.com",
        subject=kw.get("subject", "RE: timesheet"),
        conversation_id=kw.get("conversation_id", "conv-1"),
        attachments=kw.get("attachments", []),
    )


def test_reply_with_pdfs_does_not_merge_prior():
    """Sick-leave follow-up with its own PDFs — prior timesheets stay out."""
    m = _msg(attachments=[
        {"filename": "SL (1).pdf", "content_type": "application/pdf", "size": 700_000},
        {"filename": "DOH cert.pdf", "content_type": "application/pdf", "size": 4_000_000},
    ])
    assert has_extractable_attachments(m) is True


def test_approval_only_reply_may_merge_prior():
    m = _msg(attachments=[])
    assert has_extractable_attachments(m) is False


def test_inline_logo_does_not_block_merge():
    m = _msg(
        attachments=[{"filename": "logo.png", "content_type": "image/png",
                      "size": 5000, "is_inline": True}],
        body_html='<img src="cid:logo"/>',
    )
    assert has_extractable_attachments(m) is False
