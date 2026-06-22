"""
Microsoft Graph email provider — reads a shared mailbox via app-only auth.

Activate by setting in .env:
    EMAIL_PROVIDER=graph
    GRAPH_TENANT_ID=...
    GRAPH_CLIENT_ID=...
    GRAPH_CLIENT_SECRET=...
    GRAPH_MAILBOX=timesheets@yourcompany.com   # the shared mailbox
    GRAPH_FOLDER=Inbox                          # whole inbox

Requires:  pip install msal
Permission: Mail.Read (Application), admin-consented. Lock it to this one
mailbox with an Exchange Application Access Policy.

Uses ImmutableId so message/attachment ids are URL-safe in the API paths.
"""
from __future__ import annotations

import asyncio
import base64
import html
import re
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.services.email_provider.base import (
    EmailProvider,
    ProviderAttachment,
    ProviderMessage,
)

GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPE = ["https://graph.microsoft.com/.default"]

_WELLKNOWN = {"inbox", "archive", "drafts", "sentitems", "deleteditems", "junkemail"}
_DOC_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}
_MIN_INLINE_BYTES = 6000  # skip tiny inline images (signatures/logos)

_msal_app = None
_SECRET_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_graph_credentials() -> None:
    secret = (settings.graph_client_secret or "").strip()
    if not secret:
        raise RuntimeError("GRAPH_CLIENT_SECRET is not set.")
    if _SECRET_ID_RE.match(secret):
        raise RuntimeError(
            "GRAPH_CLIENT_SECRET looks like a Secret ID (UUID), not the secret Value. "
            "Azure Portal → App registrations → Certificates & secrets → create/copy "
            "the Value column (shown once at creation), not the Secret ID."
        )


def _get_msal_app():
    global _msal_app
    if _msal_app is None:
        _validate_graph_credentials()
        try:
            import msal
        except ImportError as e:
            raise RuntimeError("msal is not installed. Run: pip install msal") from e
        _msal_app = msal.ConfidentialClientApplication(
            client_id=settings.graph_client_id,
            authority=f"https://login.microsoftonline.com/{settings.graph_tenant_id}",
            client_credential=settings.graph_client_secret.strip(),
        )
    return _msal_app


async def _token() -> str:
    def _acquire() -> str:
        app = _get_msal_app()
        res = app.acquire_token_for_client(scopes=_SCOPE)
        if "access_token" not in res:
            err = res.get("error") or "unknown"
            desc = res.get("error_description") or ""
            if err == "invalid_client" and "7000215" in desc:
                raise RuntimeError(
                    "Invalid GRAPH_CLIENT_SECRET — use the secret Value from Azure "
                    "(Certificates & secrets), not the Secret ID. If the secret expired, create a new one."
                )
            raise RuntimeError(f"Graph token error: {err} — {desc}")
        return res["access_token"]
    return await asyncio.to_thread(_acquire)


async def _headers(text_body: bool = False) -> dict:
    tok = await _token()
    prefer = 'IdType="ImmutableId"'
    if text_body:
        prefer += ', outlook.body-content-type="text"'
    return {"Authorization": f"Bearer {tok}", "Prefer": prefer}


def _folder() -> str:
    f = (settings.graph_folder or "inbox").strip()
    return f.lower() if f.lower() in _WELLKNOWN else f


def _strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _classify(name: str, ctype: str, has_doc: bool) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("approv", "manager", "sign-off", "signoff")):
        return "approval_screenshot"
    if ctype in _DOC_TYPES or n.endswith((".pdf", ".docx", ".xlsx")):
        return "timesheet"
    if (ctype or "").startswith("image/") or n.endswith((".png", ".jpg", ".jpeg")):
        # an image alongside a real doc is most likely the approval screenshot;
        # an image on its own is treated as the timesheet itself.
        return "approval_screenshot" if has_doc else "timesheet"
    return "other"


def _build(msg: dict) -> ProviderMessage:
    frm = (msg.get("from") or {}).get("emailAddress") or {}
    raw = msg.get("attachments") or []
    files = [a for a in raw if str(a.get("@odata.type", "")).endswith("fileAttachment")]
    has_doc = any(
        (a.get("contentType") in _DOC_TYPES)
        or str(a.get("name", "")).lower().endswith((".pdf", ".docx", ".xlsx"))
        for a in files
    )
    atts: list[ProviderAttachment] = []
    for a in files:
        size = a.get("size") or 0
        if a.get("isInline") and 0 < size < _MIN_INLINE_BYTES:
            continue
        atts.append(ProviderAttachment(
            attachment_id=a["id"],
            filename=a.get("name") or "attachment",
            content_type=a.get("contentType") or "application/octet-stream",
            size=size,
            kind=_classify(a.get("name"), a.get("contentType"), has_doc),
        ))

    body = msg.get("body") or {}
    body_text = body.get("content") or msg.get("bodyPreview") or ""
    if body.get("contentType") == "html":
        body_text = _strip_html(body_text)

    return ProviderMessage(
        message_id=msg["id"],
        sender_name=frm.get("name") or "",
        sender_email=frm.get("address") or "",
        subject=msg.get("subject") or "(no subject)",
        received_at=_parse_dt(msg.get("receivedDateTime")),
        body_text=body_text,
        attachments=atts,
    )


_SELECT = "id,subject,from,receivedDateTime,bodyPreview,body,hasAttachments"
_EXPAND = "attachments($select=id,name,contentType,size,isInline)"


class GraphEmailProvider(EmailProvider):
    def __init__(self) -> None:
        missing = [k for k, v in {
            "GRAPH_TENANT_ID": settings.graph_tenant_id,
            "GRAPH_CLIENT_ID": settings.graph_client_id,
            "GRAPH_CLIENT_SECRET": settings.graph_client_secret,
            "GRAPH_MAILBOX": settings.graph_mailbox,
        }.items() if not (v or "").strip()]
        if missing:
            raise RuntimeError(f"Graph config missing in .env: {', '.join(missing)}")

    async def list_messages(self, query: str | None = None) -> list[ProviderMessage]:
        url = f"{GRAPH}/users/{settings.graph_mailbox}/mailFolders/{_folder()}/messages"
        params = {
            "$top": "50",
            "$orderby": "receivedDateTime desc",
            "$select": _SELECT,
            "$expand": _EXPAND,
        }
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(url, params=params, headers=await _headers(text_body=True))
            if r.status_code != 200:
                raise RuntimeError(f"Graph list error {r.status_code}: {r.text[:400]}")
            msgs = [_build(m) for m in r.json().get("value", [])]
        if query:
            q = query.lower().strip()
            msgs = [
                m for m in msgs
                if q in (m.subject or "").lower() or q in (m.sender_name or "").lower()
                or q in (m.sender_email or "").lower() or q in (m.body_text or "").lower()
            ]
        return msgs

    async def get_message(self, message_id: str) -> ProviderMessage | None:
        url = f"{GRAPH}/users/{settings.graph_mailbox}/messages/{message_id}"
        params = {"$select": _SELECT, "$expand": _EXPAND}
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(url, params=params, headers=await _headers(text_body=True))
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise RuntimeError(f"Graph get error {r.status_code}: {r.text[:400]}")
            return _build(r.json())

    async def get_attachment_bytes(self, message_id: str, attachment_id: str) -> tuple[bytes, str, str]:
        url = f"{GRAPH}/users/{settings.graph_mailbox}/messages/{message_id}/attachments/{attachment_id}"
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.get(url, headers=await _headers())
            if r.status_code == 404:
                raise FileNotFoundError(attachment_id)
            if r.status_code != 200:
                raise RuntimeError(f"Graph attachment error {r.status_code}: {r.text[:300]}")
            a = r.json()
            content = a.get("contentBytes")
            if not content:
                raise FileNotFoundError(f"No contentBytes for {attachment_id}")
            return (base64.b64decode(content),
                    a.get("name") or "attachment",
                    a.get("contentType") or "application/octet-stream")
