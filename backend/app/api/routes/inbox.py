"""Inbox routes — read emails, preview attachments, Accept/Reject/Restore."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.email_message import EmailMessage, EmailStatus
from app.models.timesheet_record import TimesheetRecord
from app.schemas import (
    AttachmentOut,
    DecisionIn,
    EmailDetail,
    EmailListItem,
)
from app.services.email_provider import get_email_provider
from app.services.ingestion import ingest_email

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
         "content_type": a.content_type, "size": a.size, "kind": a.kind}
        for a in msg.attachments
    ]
    has_approval = any(a["kind"] == "approval_screenshot" for a in atts)
    if existing:
        existing.sender_name = msg.sender_name
        existing.sender_email = msg.sender_email
        existing.subject = msg.subject
        existing.received_at = msg.received_at
        existing.body_text = msg.body_text
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
        attachments=atts,
        has_approval_screenshot=has_approval,
        status=EmailStatus.NEW,
    )
    db.add(row)
    return row


def _to_list_item(row: EmailMessage) -> EmailListItem:
    return EmailListItem(
        id=row.id,
        provider_message_id=row.provider_message_id,
        sender_name=row.sender_name,
        sender_email=row.sender_email,
        subject=row.subject,
        received_at=row.received_at,
        status=row.status,
        attachment_count=len(row.attachments or []),
        has_approval_screenshot=row.has_approval_screenshot,
    )


@router.get("", response_model=list[EmailListItem])
async def list_inbox(
    q: str | None = Query(default=None, description="search subject/sender/body"),
    status: str | None = Query(default=None, description="new | archived | ingested"),
    db: AsyncSession = Depends(get_db),
):
    provider = get_email_provider()
    messages = await provider.list_messages(q)
    rows: list[EmailMessage] = []
    for m in messages:
        rows.append(await _sync_message(db, m))
    await db.commit()
    for r in rows:
        await db.refresh(r)
    items = [_to_list_item(r) for r in rows]
    if status:
        items = [i for i in items if i.status == status]
    items.sort(key=lambda i: (i.received_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return items


@router.get("/{provider_message_id}", response_model=EmailDetail)
async def get_email(provider_message_id: str, db: AsyncSession = Depends(get_db)):
    provider = get_email_provider()
    msg = await provider.get_message(provider_message_id)
    if not msg:
        raise HTTPException(404, "Email not found")
    row = await _sync_message(db, msg)
    await db.commit()
    await db.refresh(row)
    base = _to_list_item(row)
    return EmailDetail(
        **base.model_dump(),
        body_text=row.body_text,
        attachments=[
            AttachmentOut(
                attachment_id=a["attachment_id"], filename=a["filename"],
                content_type=a["content_type"], kind=a["kind"],
            )
            for a in (row.attachments or [])
        ],
    )


@router.get("/{provider_message_id}/attachments/{attachment_id}")
async def get_attachment(provider_message_id: str, attachment_id: str):
    provider = get_email_provider()
    try:
        data, filename, content_type = await provider.get_attachment_bytes(
            provider_message_id, attachment_id
        )
    except FileNotFoundError:
        raise HTTPException(404, "Attachment not found")
    disposition = "inline" if content_type.startswith(("image/", "application/pdf")) else "attachment"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


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

    records = await ingest_email(db, row)
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
async def rerun_extraction(provider_message_id: str, db: AsyncSession = Depends(get_db)):
    """Wipe existing month folders for related records and run ingestion again."""
    row = (await db.execute(select(EmailMessage).where(EmailMessage.provider_message_id == provider_message_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Email session not found")
    if row.status != EmailStatus.INGESTED:
        raise HTTPException(400, "Only accepted emails can be re-run")

    # Find affected records to find storage paths
    recs = (await db.execute(select(TimesheetRecord).where(TimesheetRecord.source_email_id == provider_message_id))).scalars().all()
    
    from app.services import storage_provider as sp
    for r in recs:
        if r.storage_folder:
            # Delete the specific month folder
            try:
                sp.get_storage_provider().delete_folder(r.storage_folder)
            except Exception:
                pass # Already gone or permission error

    # Re-trigger ingestion (which will upsert and overwrite database rows)
    records = await ingest_email(db, row)
    return {"status": "re-ingested", "records_count": len(records)}
