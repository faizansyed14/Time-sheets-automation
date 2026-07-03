"""Inbox routes — read emails, preview attachments, Accept/Reject/Restore."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.database import get_db
from app.core.cache import cache
from app.models.email_message import EmailMessage, EmailStatus
from app.models.pipeline_file import PipelineFile
from app.models.timesheet_record import TimesheetRecord
from app.schemas import (
    AttachmentOut,
    DecisionIn,
    EmailDetail,
    EmailListItem,
    ExtractionPreviewIn,
    Page,
    PipelineFileOut,
    RerunExtractionIn,
)
from app.services.email_provider import get_email_provider
from app.services.pipeline.ingestion import IngestSelectionError, ingest_email, stage_email_extraction

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
         "content_type": a.content_type, "size": a.size, "kind": a.kind, "cid": a.cid}
        for a in msg.attachments
    ]
    has_approval = any(a["kind"] == "approval_screenshot" for a in atts)

    stmt = (
        pg_insert(EmailMessage)
        .values(
            provider_message_id=msg.message_id,
            sender_name=msg.sender_name,
            sender_email=msg.sender_email,
            subject=msg.subject,
            received_at=msg.received_at,
            body_text=msg.body_text,
            body_html=msg.body_html,
            attachments=atts,
            has_approval_screenshot=has_approval,
            status=EmailStatus.NEW,
        )
        .on_conflict_do_update(
            index_elements=["provider_message_id"],
            set_={
                # Preserve workflow fields (status/decided_at). Only refresh message data.
                "sender_name": msg.sender_name,
                "sender_email": msg.sender_email,
                "subject": msg.subject,
                "received_at": msg.received_at,
                "body_text": msg.body_text,
                "body_html": msg.body_html,
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


def _doc_count(attachments) -> int:
    return sum(1 for a in (attachments or []) if is_doc_attachment(a))


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


def _to_list_item(row: EmailMessage, extract_email_at: datetime | None = None) -> EmailListItem:
    return EmailListItem(
        id=row.id,
        provider_message_id=row.provider_message_id,
        sender_name=row.sender_name,
        sender_email=row.sender_email,
        subject=row.subject,
        received_at=row.received_at,
        status=row.status,
        attachment_count=_doc_count(row.attachments),
        has_approval_screenshot=row.has_approval_screenshot,
        extract_email_at=extract_email_at,
    )


@router.get("", response_model=Page[EmailListItem])
async def list_inbox(
    q: str | None = Query(default=None, description="search subject/sender/body (whole inbox)"),
    status: str | None = Query(default=None, description="new | archived | ingested"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, server-side searched inbox. The search hits the whole table
    (subject / sender / body) in SQL, not just the current page."""
    # Best-effort provider sync. Must never block the UI: when a background
    # scan/worker is already syncing the inbox, skip syncing here to avoid Graph
    # throttling + DB contention that would stall this endpoint.
    if offset == 0:
        lock_key = "inbox:sync:lock"
        try:
            if not await cache.exists(lock_key):
                await cache.set(lock_key, True, ttl=30)
                try:
                    provider = get_email_provider()
                    for m in await provider.list_messages(None):
                        await _sync_message(db, m)
                    await db.commit()
                except Exception:
                    # Any provider error should not break listing existing rows.
                    await db.rollback()
                finally:
                    await cache.delete(lock_key)
        except Exception:
            # Cache unavailable → just skip provider sync and serve DB results.
            pass

    base = select(EmailMessage)
    if status:
        base = base.where(EmailMessage.status == status)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        base = base.where(or_(
            func.lower(EmailMessage.subject).like(like),
            func.lower(EmailMessage.sender_name).like(like),
            func.lower(EmailMessage.sender_email).like(like),
            func.lower(EmailMessage.body_text).like(like),
        ))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(
        base.order_by(EmailMessage.received_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    times = await _extract_email_times(db, [r.provider_message_id for r in rows])
    items = [_to_list_item(r, times.get(r.provider_message_id)) for r in rows]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=offset + len(items) < total)


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

    # Resolve inline cid: images (logos, signatures, pasted screenshots) to
    # self-contained data URIs so the HTML body renders exactly like Outlook.
    from app.services.inbox.inline_images import inline_cid_images
    body_html, inline_ids = await inline_cid_images(
        provider, row.provider_message_id, row.body_html, row.attachments or [])

    base = _to_list_item(
        row,
        (await _extract_email_times(db, [row.provider_message_id])).get(row.provider_message_id),
    )
    return EmailDetail(
        **base.model_dump(),
        body_text=row.body_text,
        body_html=body_html,
        inline_attachment_ids=inline_ids,
        attachments=[
            AttachmentOut(
                attachment_id=a["attachment_id"], filename=a["filename"],
                content_type=a["content_type"], kind=a["kind"],
                cid=a.get("cid"),
            )
            for a in (row.attachments or [])
        ],
    )


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


@router.post("/{provider_message_id}/as-eml/stage", response_model=PipelineFileOut)
async def stage_as_eml(provider_message_id: str, db: AsyncSession = Depends(get_db)):
    """Run Extraction on the full .eml (manual): stages it in the pipeline as
    pending review — nothing is filed until Accept in Compare & Fix."""
    from app.api.routes.pipeline import _out as _pipeline_out
    from app.services.inbox.eml_export import build_full_eml
    from app.services.pipeline.ingestion import stage_extra_source
    row = await _email_row_or_404(db, provider_message_id)
    data, fname = await build_full_eml(get_email_provider(), row)
    tracker = await stage_extra_source(
        db, row, filename=fname, content_type="message/rfc822", data=data)
    await datacache.bust_pipeline()
    return _pipeline_out(tracker)


@router.post("/{provider_message_id}/extract-full")
async def extract_full(provider_message_id: str, db: AsyncSession = Depends(get_db)):
    """Extract Email — the one-button flow: convert the whole email to a full
    .eml, analyse EVERY sheet inside it (attachments, forwarded emails and
    their attachments, pasted body grids) with the vision model in batches,
    detect manager signatures / approval screenshots, group the results per
    employee + month, and stage one pending-review pipeline item per group.
    Nothing is filed until Accept in Compare & Fix."""
    from app.api.routes.pipeline import _out as _pipeline_out
    from app.services.agents.full_email_extract import extract_full_email
    row = await _email_row_or_404(db, provider_message_id)
    try:
        res = await extract_full_email(db, row)
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
    from app.services.extraction.file_processor import detect_file_type, to_images
    provider = get_email_provider()
    try:
        data, filename, _ct = await provider.get_attachment_bytes(
            provider_message_id, attachment_id)
    except FileNotFoundError:
        raise HTTPException(404, "Attachment not found")
    ftype = detect_file_type(filename or "", data)
    if ftype not in ("docx", "xlsx", "pdf"):
        raise HTTPException(400, f"No server render for type '{ftype}'")
    imgs = to_images(ftype, data)
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
    disposition = "inline" if content_type.startswith(("image/", "application/pdf", "message/")) else "attachment"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
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


@router.post("/{provider_message_id}/stage-extraction", response_model=list[PipelineFileOut])
async def stage_extraction(
    provider_message_id: str,
    body: ExtractionPreviewIn,
    db: AsyncSession = Depends(get_db),
):
    """Run Extraction: extract the selected attachments (and optionally the
    email body) and STAGE them in the pipeline as needs-review items carrying
    the AI-extracted leaves — without filing a record yet. The frontend then
    opens the existing Compare & Fix overlay to review/edit and Accept (file)
    or Reject (leave staged in the pipeline)."""
    from app.api.routes.pipeline import _out as _pipeline_out

    provider = get_email_provider()
    msg = await provider.get_message(provider_message_id)
    if not msg:
        raise HTTPException(404, "Email not found")
    row = await _sync_message(db, msg)
    await db.commit()
    await db.refresh(row)

    staged = await stage_email_extraction(
        db, row, attachment_ids=body.attachment_ids, extract_body=body.extract_body)
    await datacache.bust_pipeline()
    return [_pipeline_out(t) for t in staged]


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

    if not body.accepted:
        row.status = EmailStatus.ARCHIVED
        row.decided_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "archived", "records_created": 0}

    try:
        records = await ingest_email(
            db, row,
            attachment_ids=body.attachment_ids,
            approval_attachment_id=body.approval_attachment_id,
            extract_body=body.extract_body,
        )
    except IngestSelectionError as e:
        raise HTTPException(400, str(e)) from e
    await datacache.bust_pipeline()
    return {"status": "ingested", "records_created": len(records),
            "record_ids": [r.id for r in records]}


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


@router.post("/{provider_message_id}/rerun")
async def rerun_extraction(
    provider_message_id: str,
    body: RerunExtractionIn,
    db: AsyncSession = Depends(get_db),
):
    """Wipe existing month folders for related records and re-run selected attachments."""
    row = (await db.execute(select(EmailMessage).where(EmailMessage.provider_message_id == provider_message_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Email session not found")
    if row.status != EmailStatus.INGESTED:
        raise HTTPException(400, "Only accepted emails can be re-run")

    recs = (await db.execute(select(TimesheetRecord).where(TimesheetRecord.source_email_id == provider_message_id))).scalars().all()

    from app.services import storage_provider as sp
    for r in recs:
        if r.storage_folder:
            try:
                sp.get_storage_provider().delete_folder(r.storage_folder)
            except Exception:
                pass

    try:
        records = await ingest_email(
            db, row,
            attachment_ids=body.attachment_ids,
            approval_attachment_id=body.approval_attachment_id,
            extract_body=body.extract_body,
        )
    except IngestSelectionError as e:
        raise HTTPException(400, str(e)) from e
    await datacache.bust_pipeline()
    return {"status": "re-ingested", "records_count": len(records)}
