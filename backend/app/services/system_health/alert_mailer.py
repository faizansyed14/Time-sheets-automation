"""System health alert email delivery — Microsoft Graph `sendMail`, same
production transport as OTP/reminder delivery, but kept as its own small
self-contained module rather than sharing code with either: this session's
standing rule is to never let one email feature's changes risk another's,
and a health alert is exactly the kind of thing that must keep working even
while someone is mid-debugging the reminder or OTP code paths. The
token-fetch + sendMail shape is duplicated (under 20 lines) rather than
factored out, same as reminders/mailer.py does relative to auth/email_otp.py.

Dev/no-creds: logs the email and returns True (so the whole feature is
testable without a real mailbox), matching the other two mailers' fallback.

Note the one real limitation this implies: if Microsoft Graph ITSELF is the
thing that's broken (see monitor.py's "graph" check), the alert about that
travels over the very transport that's failing. monitor.py's run_health_check
therefore never depends on this send succeeding to also flip the DB status —
the row always reflects the real check result even if the email attempt
raises. There's no second, Graph-independent delivery channel here; the
"graph is down" case is genuinely best-effort by construction, and the admin
page's health card (see api/routes/system_health.py) is the reliable fallback
for that specific case.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

log = logging.getLogger("system_health.mailer")

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


def alert_recipient() -> str | None:
    addr = (settings.system_health_alert_email or settings.default_admin_email or "").strip()
    return addr or None


def send_health_alert_email(subject: str, html_body: str) -> None:
    """Synchronous send. Raises on failure — callers (monitor.py) log this
    and move on; a failed alert send must never crash the health check
    itself (that would be its own silent failure mode)."""
    to_email = alert_recipient()
    if not to_email or "@" not in to_email:
        raise ValueError(
            "No system_health_alert_email or default_admin_email configured "
            "— nowhere to send this alert."
        )
    if not _graph_configured():
        log.warning("[DEV HEALTH ALERT] -> %s | %s", to_email, subject)
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
