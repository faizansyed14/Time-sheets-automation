"""
Ingestion pipeline — shared by BOTH the email "Accept" action and the Upload page.

Core unit: ingest_timesheet_bytes(...) takes one timesheet's bytes and:
  1. extracts leave data (LLM)            -> buckets + validation summary
  2. matches identity vs all_employee_data
  3. files sheet + (optional) approval + extraction_result.json under
     <Manager>/<Employee>/<Month-Year>/   (falls back to "Unassigned" if no manager)
  4. upserts a TimesheetRecord (dedupe on employee + month + year)

ingest_email(...) reads the approval screenshot once, then calls the core unit per
timesheet attachment. ingest_upload(...) calls the core unit for a single uploaded file.

Extraction is wrapped so a failure (missing API key, API/network error, unreadable
file) NEVER returns a 500. Instead the timesheet is filed and a 'manual_review'
record is created with the reason in the summary, so the reviewer sees what happened.
"""
from __future__ import annotations

import calendar
import json as _json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage, EmailStatus
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord, ValidationStatus
from app.services import matching
from app.services import storage_provider as sp
from app.services.email_provider import get_email_provider
from app.services.extraction import get_extraction_engine
from app.services.extraction.base import ApprovalExtraction, TimesheetExtraction


async def _safe_extract(engine, data, filename, content_type, source_id, attachment_id) -> TimesheetExtraction:
    """Run the extraction engine; on ANY failure return a flagged review record
    instead of letting the exception bubble up to a 500."""
    try:
        return await engine.extract_timesheet(
            data, filename, content_type, source_id or "", attachment_id or filename
        )
    except Exception as e:  # missing key, API error, bad file, etc.
        return TimesheetExtraction(
            employee_id=None, employee_name=None, month=0, year=0,
            validation_status=ValidationStatus.MANUAL_REVIEW,
            summary=f"Extraction failed and needs manual review: {str(e)[:200]}",
            hr_flags=[f"Extraction error: {str(e)[:200]}"],
        )


async def _safe_approval(engine, data, source_id, attachment_id) -> ApprovalExtraction:
    try:
        return await engine.extract_approval(data, source_id or "", attachment_id or "")
    except Exception as e:
        return ApprovalExtraction(detected=False, detail=f"Could not read approval ({str(e)[:120]}).")


async def ingest_timesheet_bytes(
    db: AsyncSession, *, data: bytes, filename: str, content_type: str,
    approval_detected: bool, approval_detail: str, approval_bytes: bytes | None,
    approval_name: str, source_id: str | None, attachment_id: str | None = None,
) -> TimesheetRecord:
    engine = get_extraction_engine()
    ext = await _safe_extract(engine, data, filename, content_type, source_id, attachment_id)

    matched, note = await matching.match_employee(db, ext.employee_id, ext.employee_name)
    employee_name = matched.name if matched else (ext.employee_name or "Unknown")
    account_manager = matched.account_manager if matched else None

    cal_days = None
    if 1 <= ext.month <= 12 and ext.year:
        cal_days = calendar.monthrange(ext.year, ext.month)[1]

    # ---- file on disk (best-effort; never blocks record creation) ----
    folder_rel = None
    try:
        sp.save_file(account_manager, employee_name, ext.month, ext.year, filename, data)
        if approval_bytes is not None:
            sp.save_file(account_manager, employee_name, ext.month, ext.year, approval_name, approval_bytes)
        sp.save_text(account_manager, employee_name, ext.month, ext.year, "extraction_result.json", _json.dumps({
            "employee": {"extracted_id": ext.employee_id, "extracted_name": ext.employee_name,
                         "matched_id": matched.employee_id if matched else None,
                         "matched_name": matched.name if matched else None,
                         "dco_number": matched.dco_number if matched else None,
                         "account_manager": account_manager,
                         "match_note": note},
            "period": {"month": ext.month, "year": ext.year},
            "leaves": {"annual": ext.annual_leave_dates, "remote": ext.remote_work_dates,
                       "sick": ext.sick_leave_dates, "unpaid": ext.unpaid_leave_dates,
                       "absent": ext.absent_dates, "public_holiday": ext.public_holiday_dates},
            "validation": {"status": ext.validation_status, "summary": ext.summary, "flags": ext.hr_flags},
            "approval": {"detected": approval_detected, "detail": approval_detail},
            "source": source_id, "ingested_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2, default=str))
        folder_rel = sp.folder_rel(account_manager, employee_name, ext.month, ext.year)
    except Exception:
        folder_rel = None

    # ---- upsert (dedupe on employee + month + year) ----
    existing = await _find_existing(db, matched.employee_id if matched else None,
                                    employee_name, ext.month, ext.year)
    rec = existing or TimesheetRecord()
    rec.extracted_employee_id = ext.employee_id
    rec.extracted_employee_name = ext.employee_name
    rec.matched_employee_pk = matched.id if matched else None
    rec.employee_id = matched.employee_id if matched else ext.employee_id
    rec.employee_name = employee_name
    rec.account_manager = account_manager
    rec.dco_number = matched.dco_number if matched else None
    rec.match_note = note
    rec.month = ext.month
    rec.year = ext.year
    rec.calendar_days = cal_days
    rec.annual_leave_dates = ext.annual_leave_dates
    rec.remote_work_dates = ext.remote_work_dates
    rec.sick_leave_dates = ext.sick_leave_dates
    rec.unpaid_leave_dates = ext.unpaid_leave_dates
    rec.absent_dates = ext.absent_dates
    rec.public_holiday_dates = ext.public_holiday_dates
    rec.validation_status = ext.validation_status
    rec.llm_summary = ext.summary
    rec.hr_flags = ext.hr_flags
    rec.approval_detected = approval_detected
    rec.approval_detail = approval_detail
    if not existing:
        # If ingested from a source ID that starts with "upload:", it's pending.
        # Otherwise (email flow), it's auto-approved as per requirement.
        is_upload = source_id and source_id.startswith("upload:")
        rec.approval_status = ApprovalStatus.PENDING if is_upload else ApprovalStatus.APPROVED
    rec.source_email_id = source_id
    rec.storage_folder = folder_rel
    if not existing:
        db.add(rec)
    return rec


async def ingest_email(db: AsyncSession, email: EmailMessage) -> list[TimesheetRecord]:
    provider = get_email_provider()
    engine = get_extraction_engine()
    attachments = email.attachments or []
    approval_atts = [a for a in attachments if a.get("kind") == "approval_screenshot"]
    timesheet_atts = [a for a in attachments if a.get("kind") == "timesheet"]

    approval_detected, approval_detail = False, "No approval screenshot provided."
    approval_bytes, approval_name = None, "manager_approval.png"
    if approval_atts:
        a = approval_atts[0]
        try:
            approval_bytes, approval_name, _ = await provider.get_attachment_bytes(
                email.provider_message_id, a["attachment_id"])
            ap = await _safe_approval(engine, approval_bytes, email.provider_message_id, a["attachment_id"])
            approval_detected, approval_detail = ap.detected, ap.detail
        except Exception:
            approval_bytes = None

    created: list[TimesheetRecord] = []
    for a in timesheet_atts:
        try:
            data, filename, content_type = await provider.get_attachment_bytes(
                email.provider_message_id, a["attachment_id"])
        except Exception:
            continue
        rec = await ingest_timesheet_bytes(
            db, data=data, filename=filename, content_type=content_type,
            approval_detected=approval_detected, approval_detail=approval_detail,
            approval_bytes=approval_bytes, approval_name=approval_name,
            source_id=email.provider_message_id, attachment_id=a["attachment_id"])
        created.append(rec)

    email.status = EmailStatus.INGESTED
    email.decided_at = datetime.now(timezone.utc)
    await db.commit()
    for r in created:
        await db.refresh(r)
    return created


async def ingest_upload(db: AsyncSession, *, filename: str, content_type: str, data: bytes) -> TimesheetRecord:
    rec = await ingest_timesheet_bytes(
        db, data=data, filename=filename, content_type=content_type,
        approval_detected=False, approval_detail="Uploaded manually (no email approval screenshot).",
        approval_bytes=None, approval_name="manager_approval.png",
        source_id=f"upload:{filename}")
    await db.commit()
    await db.refresh(rec)
    return rec


async def _find_existing(db, employee_id, employee_name, month, year) -> TimesheetRecord | None:
    rows = (await db.execute(select(TimesheetRecord).where(
        TimesheetRecord.month == month, TimesheetRecord.year == year))).scalars().all()
    for r in rows:
        if employee_id and r.employee_id == employee_id:
            return r
        if (r.employee_name or "").strip().lower() == (employee_name or "").strip().lower():
            return r
    return None
