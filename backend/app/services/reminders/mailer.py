"""Reminder email delivery — Microsoft Graph `sendMail`, same production
transport as OTP delivery (see services/auth/email_otp.py), but kept as its
own small self-contained module rather than sharing code with that file: OTP
delivery is a security-sensitive, already-tested path and this session's
standing rule is to never disturb existing functionality while building
something new next to it. The token-fetch + sendMail shape is duplicated
(under 20 lines) rather than factored out, to keep that isolation real.

Dev/no-creds: logs the email and returns True (so the whole feature is
testable without a real mailbox), matching send_otp_email's own fallback.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

log = logging.getLogger("reminders.mailer")

_GRAPH = "https://graph.microsoft.com/v1.0"
_LOGIN = "https://login.microsoftonline.com"


def _graph_configured() -> bool:
    return bool(settings.graph_tenant_id and settings.graph_client_id and settings.graph_client_secret
                and (settings.graph_otp_sender or settings.graph_mailbox))


def _token() -> str:
    r = httpx.post(
        f"{_LOGIN}/{settings.graph_tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": settings.graph_client_id,
            "client_secret": settings.graph_client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def send_reminder_email(to_email: str, subject: str, html_body: str) -> None:
    """Synchronous send. Raises on failure — callers (service.py) catch this
    per-recipient so one bad address never stops the rest of a batch."""
    if not to_email or "@" not in to_email:
        raise ValueError(f"Not a usable email address: {to_email!r}")
    if not _graph_configured():
        log.warning("[DEV REMINDER EMAIL] -> %s | %s", to_email, subject)
        return
    sender = settings.graph_otp_sender or settings.graph_mailbox
    token = _token()
    httpx.post(
        f"{_GRAPH}/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": to_email}}],
            },
            "saveToSentItems": False,
        },
        timeout=30,
    ).raise_for_status()
