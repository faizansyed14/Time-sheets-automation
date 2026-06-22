"""
OTP email delivery.

Production: Microsoft Graph `sendMail` (app-only / client-credentials), from
`graph_otp_sender` (or `graph_mailbox`). Dev/no-creds: the message is logged and
the code stashed in the cache so the flow is testable without a real mailbox.

Called from a Celery task so a slow SMTP/Graph call never blocks the login HTTP
request (runs inline when celery_task_always_eager=true).
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

log = logging.getLogger("auth.otp")

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


def _body(code: str) -> str:
    return (
        f"<p>Your Timesheet Portal verification code is:</p>"
        f"<p style='font-size:24px;font-weight:bold;letter-spacing:4px'>{code}</p>"
        f"<p>This code expires in {settings.otp_ttl_seconds // 60} minutes. "
        f"If you didn't request it, ignore this email.</p>"
    )


def send_otp_email(email: str, code: str) -> bool:
    """Synchronous send (invoked inside the Celery task). Returns True on send."""
    if not email:
        return False
    if not _graph_configured():
        # Dev / no Graph creds: log the code (the login response also returns a
        # `debug_otp` when not running in prod, so the flow stays testable).
        log.warning("[DEV OTP] %s -> %s", email, code)
        return True
    sender = settings.graph_otp_sender or settings.graph_mailbox
    token = _token()
    httpx.post(
        f"{_GRAPH}/users/{sender}/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "message": {
                "subject": "Your verification code",
                "body": {"contentType": "HTML", "content": _body(code)},
                "toRecipients": [{"emailAddress": {"address": email}}],
            },
            "saveToSentItems": False,
        },
        timeout=30,
    ).raise_for_status()
    return True
