"""Inbox routes — read emails, preview attachments, Accept/Reject/Restore."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.config import settings
from app.core.database import get_db
from app.core.cache import cache
from app.core.http_headers import content_disposition
from app.models.email_message import EmailMessage, EmailStatus
from app.models.pipeline_file import PipelineFile
from app.schemas import (
    AttachmentOut,
    DecisionIn,
    EmailDetail,
    EmailListItem,
    Page,
    PipelineFileOut,
    ThreadDetail,
    ThreadListItem,
)
from app.services.email_provider import get_email_provider

router = APIRouter(prefix="/inbox", tags=["inbox"])

# Pipeline tag written by full_email_extract — used to detect prior Extract Email runs.
_EXTRACT_EMAIL_TAG = "__email_extract__"


async def _sync_message(db: AsyncSession, msg) -> EmailMessage:
    """Upsert a provider message into EmailMessage.

    Must be concurrency-safe: multiple requests may sync the same provider id at
    the same time (open inbox, click AI check, ai-check-all, etc.). Use a single
    INSERT .. ON CONFLICT .. DO UPDATE to avoid unique violations.
    """
    atts = [
        {"attachment_id": a.attachment_id, "filename": a.filename,
         "content_type": a.content_type, "size": a.size, "kind": a.kind, "cid": a.cid,
         "is_inline": a.is_inline}
        for a in msg.attachments
    ]
    has_approval = any(a["kind"] == "approval_screenshot" for a in atts)

    insert_stmt = pg_insert(EmailMessage).values(
        provider_message_id=msg.message_id,
        conversation_id=msg.conversation_id,
        sender_name=msg.sender_name,
        sender_email=msg.sender_email,
        to_recipients=msg.to_recipients or [],
        cc_recipients=msg.cc_recipients or [],
        subject=msg.subject,
        received_at=msg.received_at,
        body_text=msg.body_text,
        body_html=msg.body_html,
        attachments=atts,
        has_approval_screenshot=has_approval,
        status=EmailStatus.NEW,
    )
    stmt = (
        insert_stmt
        .on_conflict_do_update(
            index_elements=["provider_message_id"],
            set_={
                # Preserve workflow fields (status/decided_at). Only refresh message data.
                # COALESCE: never let a resync null out a previously-known
                # conversation_id if this particular call somehow has none.
                "conversation_id": func.coalesce(
                    insert_stmt.excluded.conversation_id, EmailMessage.conversation_id),
                "sender_name": msg.sender_name,
                "sender_email": msg.sender_email,
                "to_recipients": msg.to_recipients or [],
                "cc_recipients": msg.cc_recipients or [],
                "subject": msg.subject,
                "received_at": msg.received_at,
                "body_text": msg.body_text,
                "body_html": func.coalesce(
                    insert_stmt.excluded.body_html, EmailMessage.body_html),
                "attachments": atts,
                "has_approval_screenshot": has_approval,
            },
        )
        .returning(EmailMessage.id)
    )
    await db.execute(stmt)
    row = (
        await db.execute(
            select(EmailMessage).where(EmailMessage.provider_message_id == msg.message_id)
        )
    ).scalar_one()
    return row


_SYNC_LOCK_KEY = "inbox:sync:lock"
_SYNC_FRESH_KEY = "inbox:sync:fresh"
_SYNC_LAST_KEY = "inbox:sync:last"   # epoch seconds of the last successful sync
# Re-fetch window overlap: clock skew / out-of-order receivedDateTime; the
# upsert dedupes anything fetched twice.
_SYNC_OVERLAP = timedelta(minutes=10)


async def _sync_inbox(db: AsyncSession) -> None:
    """Throttled, incremental provider sync. Never blocks the UI on a full
    mailbox download:

    - fresh (synced < INBOX_SYNC_MIN_INTERVAL_SECONDS ago) → no-op, serve DB;
    - otherwise ask the provider only for messages received after the LAST
      SUCCESSFUL SYNC (one small request). A full folder crawl happens only
      when there is no sync marker (first boot / cache flushed);
    - any provider/cache error → serve existing DB rows, never raise.
    """
    try:
        if await cache.exists(_SYNC_FRESH_KEY) or await cache.exists(_SYNC_LOCK_KEY):
            return
        await cache.set(_SYNC_LOCK_KEY, True, ttl=60)
    except Exception:
        return  # cache layer down → skip sync, DB rows still serve
    try:
        last = await cache.get(_SYNC_LAST_KEY)
        since = (datetime.fromtimestamp(float(last), tz=timezone.utc) - _SYNC_OVERLAP) if last else None
        started_at = datetime.now(timezone.utc).timestamp()
        provider = get_email_provider()
        for m in await provider.list_messages(None, since=since):
            await _sync_message(db, m)
        await db.commit()
        await cache.set(_SYNC_LAST_KEY, started_at)
        await cache.set(_SYNC_FRESH_KEY, True, ttl=settings.inbox_sync_min_interval_seconds)
    except Exception:
        await db.rollback()
    finally:
        try:
            await cache.delete(_SYNC_LOCK_KEY)
        except Exception:
            pass


# Document attachments that go through extraction. Images/logos/screenshots are
# excluded from the inbox attachment count (the user wants only real files).
_DOC_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.ms-excel",
    "message/rfc822",
    "application/eml",
}
_DOC_EXTS = (".pdf", ".docx", ".xlsx", ".doc", ".xls", ".eml")


def is_doc_attachment(a) -> bool:
    """A real document (pdf/docx/xlsx/eml) — not an inline image, logo, or screenshot."""
    if not isinstance(a, dict):
        return False
    ct = (a.get("content_type") or "").lower()
    fn = (a.get("filename") or "").lower()
    if ct.startswith("image/"):
        return False
    return ct in _DOC_CONTENT_TYPES or fn.endswith(_DOC_EXTS)


def _is_image_attachment(a) -> bool:
    if not isinstance(a, dict):
        return False
    return (a.get("content_type") or "").lower().startswith("image/")


# Auto-generated names for images embedded in a signature/body, never a real
# attachment: Outlook's own body-image naming ("image007.png", "Outlook-…"),
# and signature-management add-ins that inject template icons named
# "C2_signature_<logo|facebook|instagram|linkedin|x|youtube|banner>_<uuid>".
_GENERIC_INLINE_RE = re.compile(
    r"^(image\d{2,3}\.(png|jpe?g|gif)|outlook-.+\.(png|jpe?g|gif|bmp)"
    r"|c2_signature_.+\.(png|jpe?g|gif))$", re.I)


def _is_body_junk_image(a, body_html: str | None) -> bool:
    """Signature/logo images living in the body — Outlook doesn't count these
    as attachments, and neither do we (list count + detail chips agree).

    Four signals, checked in order of trust:
      - the image is smaller than MIN_IMAGE_ATTACHMENT_KB (logos/icons —
        real screenshots of sheets are far larger);
      - Graph's own `isInline` flag (authoritative — set at sync time from
        the provider, not guessed from a filename);
      - a CID that's actually referenced in the HTML body (only available
        after the detail view's full resync — the LIST fetch's $select
        can't include contentId, Graph 400s on it for the base type); or
      - a filename matching a known auto-generated body-image pattern, for
        rows synced before `is_inline` existed."""
    if not _is_image_attachment(a):
        return False
    size = a.get("size")
    if isinstance(size, (int, float)) and 0 < size < settings.min_image_attachment_kb * 1024:
        return True
    if a.get("is_inline") is True:
        return True
    cid = (a.get("cid") or "").strip().strip("<>")
    if cid and body_html and f"cid:{cid}" in body_html:
        return True
    return bool(_GENERIC_INLINE_RE.match(a.get("filename") or ""))


def _doc_count(attachments, body_html: str | None = None) -> int:
    """Paperclip count: documents plus REAL image files (Outlook-style)."""
    return sum(
        1 for a in (attachments or [])
        if is_doc_attachment(a)
        or (_is_image_attachment(a) and not _is_body_junk_image(a, body_html))
    )


async def _extract_email_times(db: AsyncSession, msg_ids: list[str]) -> dict[str, datetime]:
    """Latest Extract Email timestamp per inbox message (from staged pipeline items)."""
    if not msg_ids:
        return {}
    rows = (await db.execute(
        select(PipelineFile.source_id, func.max(PipelineFile.updated_at))
        .where(
            PipelineFile.source_kind == "email",
            PipelineFile.source_id.in_(msg_ids),
            PipelineFile.attachment_id.like(f"{_EXTRACT_EMAIL_TAG}%"),
        )
        .group_by(PipelineFile.source_id)
    )).all()
    return {sid: at for sid, at in rows if sid}


def _to_list_item(
    row: EmailMessage, extract_email_at: datetime | None = None,
    thread_message_count: int = 1,
) -> EmailListItem:
    visible_atts = [
        AttachmentOut(
            attachment_id=a["attachment_id"], filename=a["filename"],
            content_type=a["content_type"], kind=a["kind"],
            cid=a.get("cid"), is_inline=a.get("is_inline"), size=a.get("size"),
        )
        for a in (row.attachments or [])
        if (
            is_doc_attachment(a)
            or (_is_image_attachment(a) and not _is_body_junk_image(a, row.body_html))
        )
    ]
    return EmailListItem(
        id=row.id,
        provider_message_id=row.provider_message_id,
        sender_name=row.sender_name,
        sender_email=row.sender_email,
        subject=row.subject,
        received_at=row.received_at,
        status=row.status,
        attachment_count=_doc_count(row.attachments, row.body_html),
        has_approval_screenshot=row.has_approval_screenshot,
        extract_email_at=extract_email_at,
        no_sheets_found_at=row.no_sheets_found_at,
        no_sheets_note=row.no_sheets_note,
        attachments=visible_atts,
        conversation_id=row.conversation_id,
        thread_id=row.conversation_id or row.id,
        thread_message_count=thread_message_count,
    )


def _apply_inbox_filters(base, q: str | None, status: str | None):
    """Shared status/search filtering for both the flat and threaded list."""
    if status == "extracted":
        # Not a lifecycle status — "Extract Email has been run on this email",
        # the same condition that shows the green Extracted badge. Matches any
        # new/ingested/archived row with an extract-tagged pipeline item.
        base = base.where(
            select(PipelineFile.id).where(
                PipelineFile.source_kind == "email",
                PipelineFile.source_id == EmailMessage.provider_message_id,
                PipelineFile.attachment_id.like(f"{_EXTRACT_EMAIL_TAG}%"),
            ).exists()
        )
    elif status == "no_sheets":
        # Extract Email was run and found nothing to stage — surfaced so it
        # never needs to be reprocessed just to rediscover the same result.
        base = base.where(EmailMessage.no_sheets_found_at.isnot(None))
    elif status:
        base = base.where(EmailMessage.status == status)
    if q and q.strip():
        # Sender-only search: every word must match the sender's name or
        # address, in any order — "ritta ibrahim" or "ritta.m.ibrahim@gmail.com".
        # Subject/body/attachments are deliberately NOT searched.
        for term in q.strip().lower().split()[:8]:
            esc = term.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_")
            like = f"%{esc}%"
            base = base.where(or_(
                func.lower(EmailMessage.sender_name).like(like, escape="\\"),
                func.lower(EmailMessage.sender_email).like(like, escape="\\"),
            ))
    return base


@router.get("", response_model=Page[EmailListItem])
async def list_inbox(
    q: str | None = Query(default=None, description="search the sender's name or email address — every word must match (any order)"),
    status: str | None = Query(default=None, description="new | archived | ingested | extracted | no_sheets"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, server-side searched inbox — one row per MESSAGE. Kept for
    any caller that wants the flat view; the Inbox page itself uses
    GET /inbox/threads (one row per Outlook-style conversation)."""
    if offset == 0:
        await _sync_inbox(db)

    base = _apply_inbox_filters(select(EmailMessage), q, status)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(EmailMessage.received_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    times = await _extract_email_times(db, [r.provider_message_id for r in rows])
    items = [_to_list_item(r, times.get(r.provider_message_id)) for r in rows]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=offset + len(items) < total)


@router.get("/threads", response_model=Page[ThreadListItem])
async def list_threads(
    q: str | None = Query(default=None, description="search the sender's name or email address — every word must match (any order)"),
    status: str | None = Query(default=None, description="new | archived | ingested | extracted | no_sheets"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Outlook-style conversation view: one row per THREAD (grouped by Graph
    conversationId; a message with none is its own singleton thread), showing
    the newest message's summary + how many messages the thread has. Same
    search/status semantics as GET /inbox, but matching ANY message in the
    thread — e.g. searching a manager's name finds the thread even when the
    newest message is from the employee."""
    if offset == 0:
        await _sync_inbox(db)

    thread_key = func.coalesce(EmailMessage.conversation_id, EmailMessage.id)

    # Every message that matches the filters, tagged with its thread key —
    # this is what decides WHICH threads qualify (any matching message pulls
    # the whole thread in).
    matching = _apply_inbox_filters(
        select(EmailMessage.id, thread_key.label("thread_key")), q, status
    ).subquery()

    # Total message count per thread — over ALL messages in the conversation,
    # not just the ones that matched the filter (a search hit on an old reply
    # should still show the thread's true message count).
    counts = (
        select(thread_key.label("thread_key"), func.count(EmailMessage.id).label("message_count"))
        .group_by(thread_key)
        .subquery()
    )

    # One row per qualifying thread = its newest message (Postgres DISTINCT ON).
    newest = (
        select(EmailMessage)
        .where(thread_key.in_(select(matching.c.thread_key)))
        .distinct(thread_key)
        .order_by(thread_key, EmailMessage.received_at.desc())
        .subquery()
    )
    newest_orm = select(EmailMessage).select_from(
        newest.join(EmailMessage, EmailMessage.id == newest.c.id)
    )

    total = (await db.execute(
        select(func.count()).select_from(select(matching.c.thread_key).distinct().subquery())
    )).scalar_one()

    rows_with_counts = (await db.execute(
        newest_orm.add_columns(counts.c.message_count)
        .join(counts, counts.c.thread_key == func.coalesce(EmailMessage.conversation_id, EmailMessage.id))
        .order_by(EmailMessage.received_at.desc())
        .limit(limit).offset(offset)
    )).all()

    rows = [r[0] for r in rows_with_counts]
    times = await _extract_email_times(db, [r.provider_message_id for r in rows])
    items = [
        _to_list_item(row, times.get(row.provider_message_id), thread_message_count=count)
        for row, count in rows_with_counts
    ]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=offset + len(items) < total)


async def _build_email_detail(db: AsyncSession, provider, row: EmailMessage) -> EmailDetail:
    """EmailDetail for an ALREADY-SYNCED row — resolves inline cid: images to
    data URIs so the body renders exactly like Outlook. Shared by the single-
    message detail endpoint and the thread view (one call per message)."""
    from app.services.inbox.inline_images import inline_cid_images
    body_html, inline_ids = await inline_cid_images(
        provider, row.provider_message_id, row.body_html, row.attachments or [])

    base = _to_list_item(
        row,
        (await _extract_email_times(db, [row.provider_message_id])).get(row.provider_message_id),
    )
    # Same visible set as the list row (docs + real images; logos/tiny
    # images already stripped by `_is_body_junk_image`). Always include size
    # so the frontend can re-apply the threshold if needed.
    return EmailDetail(
        **base.model_dump(exclude={"attachments"}),
        body_text=row.body_text,
        body_html=body_html,
        to_recipients=row.to_recipients or [],
        cc_recipients=row.cc_recipients or [],
        inline_attachment_ids=inline_ids,
        attachments=base.attachments,
    )


@router.get("/{provider_message_id}", response_model=EmailDetail)
async def get_email(
    provider_message_id: str,
    db: AsyncSession = Depends(get_db),
):
    provider = get_email_provider()
    msg = await provider.get_message(provider_message_id)
    if not msg:
        raise HTTPException(404, "Email not found")
    row = await _sync_message(db, msg)
    await db.commit()
    await db.refresh(row)
    return await _build_email_detail(db, provider, row)


@router.get("/{provider_message_id}/thread", response_model=ThreadDetail)
async def get_thread(
    provider_message_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Outlook-style conversation view: every message in this email's thread,
    oldest first. Fetches live from the provider (not just the local DB) so a
    message outside the incremental-sync window — e.g. the original message
    with the timesheet attachment, replied to weeks later — is never silently
    missing from the history."""
    provider = get_email_provider()
    anchor_msg = await provider.get_message(provider_message_id)
    if not anchor_msg:
        raise HTTPException(404, "Email not found")
    anchor_row = await _sync_message(db, anchor_msg)
    await db.commit()
    await db.refresh(anchor_row)

    thread_id = anchor_row.conversation_id or anchor_row.id
    if anchor_row.conversation_id:
        thread_msgs = await provider.list_thread_messages(anchor_row.conversation_id)
    else:
        thread_msgs = []
    if not thread_msgs:
        # No conversation_id, or the provider doesn't support thread lookup —
        # the anchor message is the whole "thread".
        rows = [anchor_row]
    else:
        rows = []
        for m in thread_msgs:
            # Incremental inbox sync stores plain text only — re-fetch any
            # thread message that still lacks HTML so signatures/fonts render.
            if not m.body_html:
                full = await provider.get_message(m.message_id)
                if full:
                    m = full
            rows.append(await _sync_message(db, m))
        await db.commit()
        for r in rows:
            await db.refresh(r)
        rows.sort(key=lambda r: r.received_at or anchor_row.received_at)

    messages = [await _build_email_detail(db, provider, r) for r in rows]
    return ThreadDetail(thread_id=thread_id, messages=messages)


async def _email_row_or_404(db: AsyncSession, provider_message_id: str) -> EmailMessage:
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == provider_message_id))).scalar_one_or_none()
    if not row:
        # Sync once from the provider before giving up.
        msg = await get_email_provider().get_message(provider_message_id)
        if not msg:
            raise HTTPException(404, "Email not found")
        row = await _sync_message(db, msg)
        await db.commit()
        await db.refresh(row)
    return row


@router.get("/{provider_message_id}/as-eml")
async def download_as_eml(provider_message_id: str, db: AsyncSession = Depends(get_db)):
    """The COMPLETE email as one .eml — headers, body, every attachment and
    nested forwarded email. Graph serves the byte-exact original MIME; the mock
    provider gets a faithful reconstruction."""
    from app.services.inbox.eml_export import build_full_eml
    row = await _email_row_or_404(db, provider_message_id)
    data, fname = await build_full_eml(get_email_provider(), row)
    from urllib.parse import quote
    return Response(
        content=data,
        media_type="message/rfc822",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/{provider_message_id}/as-eml/preview")
async def preview_as_eml(provider_message_id: str, db: AsyncSession = Depends(get_db)):
    """Parsed view of the exported .eml (same JSON shape as other EML previews)."""
    from app.services.extraction.eml_parser import parse_eml
    from app.services.inbox.eml_export import build_full_eml
    row = await _email_row_or_404(db, provider_message_id)
    data, _ = await build_full_eml(get_email_provider(), row)
    return parse_eml(data)


@router.get("/{provider_message_id}/llm-preview")
async def preview_llm_payload(
    provider_message_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Audit view: subject/body/prompt text that Extract Email would send to
    the vision model AFTER PII redaction. Does not call the LLM. When the
    selected message has its own attachments, only that message is shown;
    otherwise the prior thread message is merged (approval-only reply)."""
    from app.services.agents.full_email_extract import preview_llm_egress
    from app.services.extract_email.thread_scope import prior_message_for_merge
    row = await _email_row_or_404(db, provider_message_id)
    prior_row = await prior_message_for_merge(db, row)
    return await preview_llm_egress(row, prior_email=prior_row)


async def _resolve_thread_anchor_and_prior(
    db: AsyncSession, row: EmailMessage,
) -> tuple[EmailMessage, EmailMessage | None]:
    """Deprecated — kept for any external callers. Prefer prior_message_for_merge."""
    from app.services.extract_email.thread_scope import prior_message_for_merge
    prior = await prior_message_for_merge(db, row)
    return row, prior


@router.post("/{provider_message_id}/extract-full")
async def extract_full(provider_message_id: str,
                       db: AsyncSession = Depends(get_db)):
    """Extract Email — the one-button flow: convert the whole email to a full
    .eml, analyse EVERY sheet inside it (attachments, forwarded emails and
    their attachments, pasted body grids) with the vision model (one call per
    sheet), detect manager signatures / approval screenshots, group the results
    per employee + month, and stage one pending-review pipeline item per group.

    Thread-aware (narrow): when the selected message has no document
    attachments (e.g. an "Approved." reply), the message immediately before
    it in the same conversation is merged too — so approval can match the
    original timesheet. Messages with their own PDFs/DOCX are extracted alone.
    Nothing is filed until Accept in Compare & Fix."""
    from app.api.routes.pipeline import _out as _pipeline_out
    from app.services.agents.full_email_extract import extract_full_email
    from app.services.extract_email.thread_scope import prior_message_for_merge
    row = await _email_row_or_404(db, provider_message_id)
    prior_row = await prior_message_for_merge(db, row)
    try:
        res = await extract_full_email(db, row, prior_email=prior_row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Full-email extraction failed: {str(e)[:300]}")
    await datacache.bust_pipeline()
    return {
        "staged": [_pipeline_out(t) for t in res["staged"]],
        "groups": res["groups"],
        "sheets": res["sheets"],
        "employees": res["employees"],
        "approval": res["approval"],
        "message": res["message"],
    }


@router.post("/{provider_message_id}/extract-full/stream")
async def extract_full_streamed(provider_message_id: str):
    """Same as extract-full, but streams live progress (Server-Sent Events):
    one frame per pipeline stage — unpack, format detected, each LLM call,
    approval, grouping, and the auto-accept decision per employee — then a
    final `done` frame with the same result payload as extract-full."""
    from fastapi.responses import StreamingResponse

    from app.api.routes.pipeline import _out as _pipeline_out
    from app.core.database import SessionLocal
    from app.services.agents.full_email_extract import extract_full_email
    from app.services.extract_email.streaming import sse_events
    from app.services.extract_email.thread_scope import prior_message_for_merge

    async def run() -> dict:
        # Own DB session — the request one closes before the stream finishes.
        async with SessionLocal() as db:
            row = await _email_row_or_404(db, provider_message_id)
            prior_row = await prior_message_for_merge(db, row)
            res = await extract_full_email(db, row, prior_email=prior_row)
            await datacache.bust_pipeline()
            return {
                "staged": [_pipeline_out(t).model_dump(mode="json") for t in res["staged"]],
                "groups": res["groups"],
                "employees": res["employees"],
                "approval": res["approval"],
                "message": res["message"],
            }

    return StreamingResponse(sse_events(run), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class SaveEmlToVaultIn(BaseModel):
    manager: str
    employee: str
    month: int
    year: int


@router.post("/{provider_message_id}/as-eml/save-to-vault")
async def save_eml_to_vault(
    provider_message_id: str, body: SaveEmlToVaultIn, db: AsyncSession = Depends(get_db),
):
    """Save the full .eml straight into the File Vault under the chosen
    Manager / Employee / Month folder."""
    if not (1 <= body.month <= 12) or body.year < 2000:
        raise HTTPException(400, "Invalid month/year")
    from app.services import storage_provider as sp
    from app.services.inbox.eml_export import build_full_eml
    row = await _email_row_or_404(db, provider_message_id)
    data, fname = await build_full_eml(get_email_provider(), row)
    rel = sp.save_file(body.manager.strip() or "Unknown",
                       body.employee.strip() or "Unknown",
                       body.month, body.year, fname, data)
    return {"saved": True, "path": rel, "filename": fname}


# Server-side render of DOCX/XLSX attachments to page images (previews that
# work in every browser; the original file stays downloadable).
@router.get("/{provider_message_id}/attachments/{attachment_id}/render")
async def render_attachment(
    provider_message_id: str, attachment_id: str,
    page: int = Query(default=1, ge=1, le=50),
):
    from app.services.extraction.file_processor import detect_file_type, to_page_images
    provider = get_email_provider()
    try:
        data, filename, _ct = await provider.get_attachment_bytes(
            provider_message_id, attachment_id)
    except FileNotFoundError:
        raise HTTPException(404, "Attachment not found")
    ftype = detect_file_type(filename or "", data)
    if ftype not in ("docx", "xlsx", "pdf"):
        raise HTTPException(400, f"No server render for type '{ftype}'")
    try:
        imgs = to_page_images(ftype, data)
    except Exception:
        raise HTTPException(422, "Could not render this file")
    if not imgs:
        raise HTTPException(422, "Could not render this file")
    idx = min(page, len(imgs)) - 1
    return Response(content=imgs[idx], media_type="image/jpeg",
                    headers={"X-Page-Count": str(len(imgs))})


@router.get("/{provider_message_id}/attachments/{attachment_id}")
async def get_attachment(provider_message_id: str, attachment_id: str):
    provider = get_email_provider()
    try:
        data, filename, content_type = await provider.get_attachment_bytes(
            provider_message_id, attachment_id
        )
    except FileNotFoundError:
        raise HTTPException(404, "Attachment not found")
    from app.services.extraction.file_processor import content_type_for, detect_file_type
    # Graph often sends application/octet-stream — browsers then download
    # instead of previewing PDFs in an iframe. Sniff the real type.
    media = content_type_for(filename or "", data, content_type)
    ftype = detect_file_type(filename or "", data)
    inline = ftype in ("pdf", "image", "eml") or media.startswith(("image/", "application/pdf", "message/"))
    disposition = "inline" if inline else "attachment"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": content_disposition(disposition, filename)},
    )


@router.get("/{provider_message_id}/attachments/{attachment_id}/eml-preview")
async def get_attachment_eml_preview(provider_message_id: str, attachment_id: str):
    """Parse an EML attachment and return its structured content as JSON."""
    provider = get_email_provider()
    try:
        data, filename, _ct = await provider.get_attachment_bytes(
            provider_message_id, attachment_id
        )
    except FileNotFoundError:
        raise HTTPException(404, "Attachment not found")
    if not filename.lower().endswith(".eml"):
        raise HTTPException(400, "Not an EML file")
    from app.services.extraction.eml_parser import parse_eml
    return parse_eml(data)


@router.post("/{provider_message_id}/decision")
async def decide(provider_message_id: str, body: DecisionIn, db: AsyncSession = Depends(get_db)):
    provider = get_email_provider()
    msg = await provider.get_message(provider_message_id)
    if not msg:
        raise HTTPException(404, "Email not found")
    row = await _sync_message(db, msg)
    await db.commit()
    await db.refresh(row)

    if row.status == EmailStatus.INGESTED:
        raise HTTPException(409, "Email already ingested — cannot re-decide.")
    if row.status == EmailStatus.ARCHIVED and not body.accepted:
        raise HTTPException(409, "Email already archived. Use /restore to reopen.")

    if body.accepted:
        # Instant filing is gone by design: every extraction goes through
        # Compare & Fix review. Use Extract Email (full or selected).
        raise HTTPException(
            400, "Direct accept was removed — run Extract Email and review the "
                 "staged items instead.")

    row.status = EmailStatus.ARCHIVED
    row.decided_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "archived", "records_created": 0}


@router.post("/{provider_message_id}/restore")
async def restore_email(provider_message_id: str, db: AsyncSession = Depends(get_db)):
    """Restore an archived email back to 'new' so it can be accepted."""
    row = (
        await db.execute(
            select(EmailMessage).where(EmailMessage.provider_message_id == provider_message_id)
        )
    ).scalar_one_or_none()

    if not row:
        # Try by internal id as well
        row = (
            await db.execute(
                select(EmailMessage).where(EmailMessage.id == provider_message_id)
            )
        ).scalar_one_or_none()

    if not row:
        raise HTTPException(404, "Email not found")
    if row.status != EmailStatus.ARCHIVED:
        raise HTTPException(409, f"Email status is '{row.status}', only archived emails can be restored.")

    row.status = EmailStatus.NEW
    row.decided_at = None
    await db.commit()
    await db.refresh(row)
    return {"status": "new", "id": row.id, "provider_message_id": row.provider_message_id}


