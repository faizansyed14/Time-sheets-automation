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
_msal_app = None


def _get_msal_app():
    global _msal_app
    if _msal_app is None:
        try:
            import msal
        except ImportError as e:
            raise RuntimeError("msal is not installed. Run: pip install msal") from e
        _msal_app = msal.ConfidentialClientApplication(
            client_id=settings.graph_client_id,
            authority=f"https://login.microsoftonline.com/{settings.graph_tenant_id}",
            client_credential=settings.graph_client_secret,
        )
    return _msal_app


async def _token() -> str:
    def _acquire() -> str:
        app = _get_msal_app()
        res = app.acquire_token_for_client(scopes=_SCOPE)
        if "access_token" not in res:
            raise RuntimeError(
                f"Graph token error: {res.get('error')} — {res.get('error_description')}"
            )
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


def _is_eml(name: str, ctype: str) -> bool:
    """A forwarded email carried as a file — its nested PDF/sheet is the real
    timesheet, so the whole .eml is a TIMESHEET container (never an approval)."""
    n = (name or "").lower()
    c = (ctype or "").lower()
    return n.endswith(".eml") or c in ("message/rfc822", "application/eml")


def _is_doc(name: str, ctype: str) -> bool:
    n = (name or "").lower()
    return (ctype in _DOC_TYPES) or n.endswith((".pdf", ".docx", ".xlsx")) or _is_eml(name, ctype)


# Auto-generated signature/logo images that are never a real screenshot:
# Outlook's own body-image naming ("image007.png", "Outlook-…") and
# signature-add-in template icons ("C2_signature_facebook2_<uuid>.png").
# Same patterns the Inbox UI hides from the attachment strip — kept in sync
# so "has a real approval screenshot" means the same thing everywhere.
_GENERIC_IMAGE_RE = re.compile(
    r"^(image\d{2,3}\.(png|jpe?g|gif)|outlook-.+\.(png|jpe?g|gif|bmp)"
    r"|c2_signature_.+\.(png|jpe?g|gif))$", re.I)


def _classify(name: str, ctype: str, has_doc: bool, is_inline: bool = False,
              size: int = 0) -> str:
    n = (name or "").lower()
    # An .eml/message is a document container — classify it first so it can
    # never be mistaken for an approval screenshot or rendered as a flat image.
    if _is_eml(name, ctype):
        return "timesheet"
    if any(k in n for k in ("approv", "manager", "sign-off", "signoff")):
        return "approval_screenshot"
    if ctype in _DOC_TYPES or n.endswith((".pdf", ".docx", ".xlsx")):
        return "timesheet"
    if (ctype or "").startswith("image/") or n.endswith((".png", ".jpg", ".jpeg")):
        # Tiny images are signature logos/icons — a real screenshot of a sheet
        # is far larger than MIN_IMAGE_ATTACHMENT_KB.
        if 0 < size < settings.min_image_attachment_kb * 1024:
            return "other"
        # Graph's own "this is embedded in the body" flag is authoritative —
        # never a real screenshot regardless of name. The filename pattern is
        # a fallback for providers/rows that predate this flag.
        if is_inline or _GENERIC_IMAGE_RE.match(name or ""):
            return "other"  # signature/logo — never a real approval screenshot
        # a REAL image alongside a real doc is most likely the approval
        # screenshot; a real image on its own is treated as the timesheet itself.
        return "approval_screenshot" if has_doc else "timesheet"
    return "other"


def _parse_recipients(raw: list | None) -> list[dict]:
    out: list[dict] = []
    for r in raw or []:
        ea = (r.get("emailAddress") or {})
        email = (ea.get("address") or "").strip()
        if not email:
            continue
        name = (ea.get("name") or "").strip() or None
        out.append({"name": name, "email": email})
    return out


def _build(msg: dict) -> ProviderMessage:
    frm = (msg.get("from") or {}).get("emailAddress") or {}
    raw = msg.get("attachments") or []
    files = [a for a in raw if str(a.get("@odata.type", "")).endswith("fileAttachment")]
    # A forwarded email shows up as an itemAttachment (message/rfc822) whose own
    # body carries the real timesheet (PDF/XLSX). Graph does NOT surface that
    # nested file as a top-level attachment, so we keep the item itself and let
    # the .eml pipeline dig the timesheet out of it.
    items = [a for a in raw if str(a.get("@odata.type", "")).endswith("itemAttachment")]
    # A real document present? (PDF/Office/.eml file OR a forwarded-email item.)
    # When true, accompanying inline images are treated as approval screenshots
    # rather than hijacking extraction as the "timesheet".
    has_doc = any(_is_doc(a.get("name", ""), a.get("contentType", "")) for a in files) or bool(items)
    atts: list[ProviderAttachment] = []
    for a in files:
        size = a.get("size") or 0
        is_inline = bool(a.get("isInline"))
        atts.append(ProviderAttachment(
            attachment_id=a["id"],
            filename=a.get("name") or "attachment",
            content_type=a.get("contentType") or "application/octet-stream",
            size=size,
            kind=_classify(a.get("name"), a.get("contentType"), has_doc, is_inline,
                           size=int(size or 0)),
            cid=a.get("contentId") or None,
            is_inline=is_inline,
        ))
    for a in items:
        # Treat the forwarded email as a timesheet candidate; its bytes are
        # fetched as raw MIME (.eml) and processed by the same .eml extractor.
        name = (a.get("name") or "forwarded-email").strip() or "forwarded-email"
        if not name.lower().endswith(".eml"):
            name = f"{name}.eml"
        atts.append(ProviderAttachment(
            attachment_id=a["id"],
            filename=name,
            content_type="message/rfc822",
            size=a.get("size") or 0,
            kind="timesheet",
        ))

    body = msg.get("body") or {}
    body_content = body.get("content") or msg.get("bodyPreview") or ""
    body_html: str | None = None
    if body.get("contentType") == "html":
        body_html = body_content          # keep raw HTML for rich rendering
        body_text = _strip_html(body_content)  # plain text for search / AI check
    else:
        body_text = body_content

    return ProviderMessage(
        message_id=msg["id"],
        sender_name=frm.get("name") or "",
        sender_email=frm.get("address") or "",
        subject=msg.get("subject") or "(no subject)",
        received_at=_parse_dt(msg.get("receivedDateTime")),
        body_text=body_text,
        body_html=body_html,
        attachments=atts,
        to_recipients=_parse_recipients(msg.get("toRecipients")),
        cc_recipients=_parse_recipients(msg.get("ccRecipients")),
        conversation_id=msg.get("conversationId") or None,
    )


_SELECT = (
    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
    "bodyPreview,body,hasAttachments,conversationId"
)
# List view: base attachment type only supports these fields in $select.
# contentId is NOT selectable on the base type — Graph 400s
# ("Could not find a property named 'contentId' on type
# 'microsoft.graph.attachment'"), verified against the live API — so cid stays
# unavailable until the detail view's full resync. isInline IS on the base
# type though, and is what actually fixes the list-vs-detail count mismatch.
_EXPAND = "attachments($select=id,name,contentType,size,isInline)"
# Detail view: no $select → Graph returns all fields including contentId on fileAttachment subtype
_EXPAND_DETAIL = "attachments"


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

    async def list_messages(
        self, query: str | None = None, since: datetime | None = None,
    ) -> list[ProviderMessage]:
        url = f"{GRAPH}/users/{settings.graph_mailbox}/mailFolders/{_folder()}/messages"
        params = {
            "$top": "50",
            "$orderby": "receivedDateTime desc",
            "$select": _SELECT,
            "$expand": _EXPAND,
        }
        if since is not None:
            # Incremental sync: only messages received after `since` — one
            # small page instead of paginating the entire folder.
            iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            params["$filter"] = f"receivedDateTime gt {iso}"
        msgs: list[ProviderMessage] = []
        next_url: str | None = url
        next_params: dict | None = params
        # Safety cap — paginate the whole folder, but never loop forever.
        _MAX_SYNC = 5000
        async with httpx.AsyncClient(timeout=60) as c:
            while next_url and len(msgs) < _MAX_SYNC:
                r = await c.get(
                    next_url,
                    params=next_params,
                    headers=await _headers(text_body=True),
                )
                if r.status_code != 200:
                    raise RuntimeError(f"Graph list error {r.status_code}: {r.text[:400]}")
                body = r.json()
                msgs.extend(_build(m) for m in body.get("value", []))
                next_url = body.get("@odata.nextLink")
                next_params = None  # nextLink is a fully-qualified URL
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
        params = {"$select": _SELECT, "$expand": _EXPAND_DETAIL}
        async with httpx.AsyncClient(timeout=60) as c:
            # text_body=False → Graph returns native HTML body so we can store
            # body_html for rich rendering. body_text is derived via _strip_html.
            r = await c.get(url, params=params, headers=await _headers(text_body=False))
            if r.status_code == 404:
                return None
            if r.status_code != 200:
                raise RuntimeError(f"Graph get error {r.status_code}: {r.text[:400]}")
            return _build(r.json())

    async def get_message_mime(self, message_id: str) -> bytes | None:
        """Byte-exact original MIME (headers, body, every attachment, nested
        forwarded emails) — the highest-fidelity .eml export possible."""
        url = f"{GRAPH}/users/{settings.graph_mailbox}/messages/{message_id}/$value"
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.get(url, headers=await _headers())
            if r.status_code != 200 or not r.content:
                return None
            return r.content

    async def list_thread_messages(self, conversation_id: str) -> list[ProviderMessage]:
        """Every message in this conversation, straight from Graph (not the
        local DB) — so a thread never appears missing its original attachment
        just because that message predates the incremental-sync window.

        Mailbox-wide (NOT scoped to mailFolders/{_folder()}) — a reply or
        forward YOU sent lives in Sent Items, not Inbox, and Outlook's own
        conversation view shows it too. Scoping this to Inbox only would
        silently drop your own replies from the history."""
        cid_escaped = conversation_id.replace("'", "''")  # OData literal escaping
        url = f"{GRAPH}/users/{settings.graph_mailbox}/messages"
        params = {
            # NO $orderby here — combined with a mailbox-wide (non-folder)
            # conversationId filter, Graph 400s with "InefficientFilter" even
            # with ConsistencyLevel: eventual (verified against the live API).
            # Sorted client-side below instead.
            "$select": _SELECT,
            "$expand": _EXPAND_DETAIL,
            "$filter": f"conversationId eq '{cid_escaped}'",
            "$count": "true",
        }
        # Mailbox-wide filter needs Graph's "advanced query capabilities"
        # opt-in — folder-scoped queries (list_messages) don't need this.
        headers = await _headers(text_body=False)
        headers["ConsistencyLevel"] = "eventual"
        msgs: list[ProviderMessage] = []
        next_url: str | None = url
        next_params: dict | None = params
        async with httpx.AsyncClient(timeout=60) as c:
            while next_url and len(msgs) < 200:  # a runaway thread still can't hang the UI
                r = await c.get(next_url, params=next_params, headers=headers)
                if r.status_code != 200:
                    raise RuntimeError(f"Graph thread error {r.status_code}: {r.text[:400]}")
                body = r.json()
                msgs.extend(_build(m) for m in body.get("value", []))
                next_url = body.get("@odata.nextLink")
                next_params = None
        msgs.sort(key=lambda m: m.received_at)
        return msgs

    async def get_attachment_bytes(self, message_id: str, attachment_id: str) -> tuple[bytes, str, str]:
        url = f"{GRAPH}/users/{settings.graph_mailbox}/messages/{message_id}/attachments/{attachment_id}"
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.get(url, headers=await _headers())
            if r.status_code == 404:
                raise FileNotFoundError(attachment_id)
            if r.status_code != 200:
                raise RuntimeError(f"Graph attachment error {r.status_code}: {r.text[:300]}")
            a = r.json()
            odata = str(a.get("@odata.type", ""))
            content = a.get("contentBytes")
            # itemAttachment (a forwarded email) has no contentBytes — its raw
            # MIME (the .eml, with the timesheet PDF inside) is served at /$value.
            if odata.endswith("itemAttachment") or not content:
                rv = await c.get(f"{url}/$value", headers=await _headers())
                if rv.status_code != 200 or not rv.content:
                    raise FileNotFoundError(f"No content for {attachment_id}")
                name = (a.get("name") or "forwarded-email").strip() or "forwarded-email"
                if not name.lower().endswith(".eml"):
                    name = f"{name}.eml"
                return rv.content, name, "message/rfc822"
            return (base64.b64decode(content),
                    a.get("name") or "attachment",
                    a.get("contentType") or "application/octet-stream")
