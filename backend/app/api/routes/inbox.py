"""Inbox routes — read emails, preview attachments, Accept/Reject/Restore."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.database import get_db
from app.models.email_message import EmailMessage, EmailStatus
from app.models.timesheet_record import TimesheetRecord
from app.schemas import (
    AttachmentOut,
    DecisionIn,
    EmailAiCheckOut,
    EmailDetail,
    EmailListItem,
    ExtractionPreviewIn,
    MatchedEmployeeOut,
    Page,
    PipelineFileOut,
    RerunExtractionIn,
)
from app.services.email_provider import get_email_provider
from app.services.pipeline.ingestion import IngestSelectionError, ingest_email, stage_email_extraction

router = APIRouter(prefix="/inbox", tags=["inbox"])


async def _sync_message(db: AsyncSession, msg) -> EmailMessage:
    """Upsert a provider message into our EmailMessage table (preserving status)."""
    existing = (
        await db.execute(
            select(EmailMessage).where(EmailMessage.provider_message_id == msg.message_id)
        )
    ).scalar_one_or_none()
    atts = [
        {"attachment_id": a.attachment_id, "filename": a.filename,
         "content_type": a.content_type, "size": a.size, "kind": a.kind, "cid": a.cid}
        for a in msg.attachments
    ]
    has_approval = any(a["kind"] == "approval_screenshot" for a in atts)
    if existing:
        existing.sender_name = msg.sender_name
        existing.sender_email = msg.sender_email
        existing.subject = msg.subject
        existing.received_at = msg.received_at
        existing.body_text = msg.body_text
        existing.body_html = msg.body_html
        existing.attachments = atts
        existing.has_approval_screenshot = has_approval
        return existing
    row = EmailMessage(
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
    db.add(row)
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


def _to_list_item(row: EmailMessage) -> EmailListItem:
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
    provider = get_email_provider()
    # Discover any new provider messages so the table is current, then page
    # from the DB. (Sync runs once per request; only on the first page to keep
    # scrolling cheap.)
    if offset == 0:
        for m in await provider.list_messages(None):
            await _sync_message(db, m)
        await db.commit()

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
    items = [_to_list_item(r) for r in rows]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=offset + len(items) < total)


@router.get("/{provider_message_id}", response_model=EmailDetail)
async def get_email(
    provider_message_id: str,
    refresh_ai: bool = Query(default=False, description="Re-run inbox AI check"),
    db: AsyncSession = Depends(get_db),
):
    provider = get_email_provider()
    msg = await provider.get_message(provider_message_id)
    if not msg:
        raise HTTPException(404, "Email not found")
    row = await _sync_message(db, msg)
    await db.commit()
    await db.refresh(row)

    ai_out: EmailAiCheckOut | None = None
    if row.ai_check:
        ai_out = EmailAiCheckOut(**row.ai_check)
    # Auto-run the AI check when opening an email that has real document
    # attachments (pdf/docx/xlsx/eml) and hasn't been checked yet — or whenever
    # an explicit refresh is requested. Emails without documents are never
    # checked (nothing to extract).
    needs_check = (refresh_ai or row.ai_check is None) and _doc_count(row.attachments) > 0
    if needs_check:
        from app.services.inbox.ai_check import ensure_ai_check
        try:
            result = await ensure_ai_check(db, row, force=refresh_ai)
            await db.commit()
            await db.refresh(row)
            ai_out = EmailAiCheckOut(**result)
        except Exception:
            if row.ai_check:
                ai_out = EmailAiCheckOut(**row.ai_check)

    # Resolve inline cid: images (logos, signatures, pasted screenshots) to
    # self-contained data URIs so the HTML body renders exactly like Outlook.
    from app.services.inbox.inline_images import inline_cid_images
    body_html, inline_ids = await inline_cid_images(
        provider, row.provider_message_id, row.body_html, row.attachments or [])

    base = _to_list_item(row)
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
        ai_check=ai_out,
        ai_check_running=False,
    )


@router.get("/{provider_message_id}/body-image-preview")
async def body_image_preview(provider_message_id: str, db: AsyncSession = Depends(get_db)):
    """Preview the rendered email-body image (inline timesheet in message text)."""
    from app.services.extraction.file_processor import email_body_to_images

    row = (
        await db.execute(
            select(EmailMessage).where(EmailMessage.provider_message_id == provider_message_id)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Email not found")
    imgs = email_body_to_images(row.subject, row.body_text)
    if not imgs:
        raise HTTPException(400, "Could not render email body")
    return Response(content=imgs[0], media_type="image/jpeg")


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
