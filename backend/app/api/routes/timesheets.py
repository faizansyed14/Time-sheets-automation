"""Timesheet record routes — detail view + manager Approve/Not-approve sign-off."""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord
from app.schemas import ApprovalIn, TimesheetOut, TimesheetUpdate

router = APIRouter(prefix="/timesheets", tags=["timesheets"])


def to_out(r: TimesheetRecord) -> TimesheetOut:
    return TimesheetOut(
        id=r.id,
        employee_id=r.employee_id,
        employee_name=r.employee_name,
        account_manager=r.account_manager,
        dco_number=r.dco_number,
        match_note=r.match_note,
        month=r.month,
        year=r.year,
        calendar_days=r.calendar_days,
        annual_leave_dates=r.annual_leave_dates or [],
        remote_work_dates=r.remote_work_dates or [],
        sick_leave_dates=r.sick_leave_dates or [],
        unpaid_leave_dates=r.unpaid_leave_dates or [],
        absent_dates=r.absent_dates or [],
        public_holiday_dates=r.public_holiday_dates or [],
        annual_leave_count=r.annual_leave_count,
        remote_work_count=r.remote_work_count,
        sick_leave_count=r.sick_leave_count,
        unpaid_leave_count=r.unpaid_leave_count,
        absent_count=r.absent_count,
        public_holiday_count=r.public_holiday_count,
        validation_status=r.validation_status,
        llm_summary=r.llm_summary,
        hr_flags=r.hr_flags or [],
        approval_detected=r.approval_detected,
        approval_detail=r.approval_detail,
        approval_status=r.approval_status,
        source_email_id=r.source_email_id,
        storage_folder=r.storage_folder,
        source_files=r.source_files or [],
        source_file_count=r.source_file_count,
    )


@router.get("", response_model=list[TimesheetOut])
async def list_records(
    year: int | None = Query(default=None),
    employee_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(TimesheetRecord)
    if year:
        stmt = stmt.where(TimesheetRecord.year == year)
    if employee_id:
        stmt = stmt.where(TimesheetRecord.employee_id == employee_id)
    rows = (await db.execute(stmt.order_by(TimesheetRecord.created_at.desc()))).scalars().all()
    return [to_out(r) for r in rows]


@router.get("/{record_id}", response_model=TimesheetOut)
async def get_record(record_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(TimesheetRecord).where(TimesheetRecord.id == record_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Record not found")
    return to_out(r)


@router.post("/{record_id}/approve", response_model=TimesheetOut)
async def approve_record(record_id: str, body: ApprovalIn, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(TimesheetRecord).where(TimesheetRecord.id == record_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Record not found")
    r.approval_status = ApprovalStatus.APPROVED if body.approved else ApprovalStatus.NOT_APPROVED
    await db.commit()
    await db.refresh(r)
    return to_out(r)


@router.patch("/{record_id}", response_model=TimesheetOut)
async def update_record(record_id: str, body: TimesheetUpdate, db: AsyncSession = Depends(get_db)):
    """Edit leave buckets/dates. Re-runs validation; if no issues remain the
    record auto-returns to 'verified', otherwise it stays 'manual_review'."""
    from app.services.extraction.validation import validate as _validate
    from app.models.timesheet_record import ValidationStatus

    r = (await db.execute(select(TimesheetRecord).where(TimesheetRecord.id == record_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Record not found")

    if body.month is not None:
        r.month = body.month
    if body.year is not None:
        r.year = body.year

    def _clean(values):
        out, seen = [], set()
        for v in (values or []):
            v = str(v).strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return sorted(out)

    if body.annual_leave_dates is not None:
        r.annual_leave_dates = _clean(body.annual_leave_dates)
    if body.remote_work_dates is not None:
        r.remote_work_dates = _clean(body.remote_work_dates)
    if body.sick_leave_dates is not None:
        r.sick_leave_dates = _clean(body.sick_leave_dates)
    if body.unpaid_leave_dates is not None:
        r.unpaid_leave_dates = _clean(body.unpaid_leave_dates)
    if body.absent_dates is not None:
        r.absent_dates = _clean(body.absent_dates)
    if body.public_holiday_dates is not None:
        r.public_holiday_dates = _clean(body.public_holiday_dates)

    buckets = {
        "annual": r.annual_leave_dates or [], "remote": r.remote_work_dates or [],
        "sick": r.sick_leave_dates or [], "unpaid": r.unpaid_leave_dates or [],
        "absent": r.absent_dates or [], "public_holiday": r.public_holiday_dates or [],
    }
    cleaned, flags = _validate(buckets, r.month, r.year)
    r.annual_leave_dates = cleaned["annual"]
    r.remote_work_dates = cleaned["remote"]
    r.sick_leave_dates = cleaned["sick"]
    r.unpaid_leave_dates = cleaned["unpaid"]
    r.absent_dates = cleaned["absent"]
    r.public_holiday_dates = cleaned["public_holiday"]
    r.hr_flags = flags
    # A manual edit becomes the single source of truth for this month —
    # otherwise a later weekly-file merge would resurrect dates the reviewer
    # deliberately removed.
    from datetime import datetime as _dt2, timezone as _tz
    prior = [e.get("filename") for e in (r.source_files or [])
             if e.get("filename") and e.get("key") != "manual_edit"]
    r.source_files = [{
        "key": "manual_edit",
        "filename": "Manual edit" + (f" (was: {', '.join(prior)})" if prior else ""),
        "source_id": None, "attachment_id": None,
        "ingested_at": _dt2.now(_tz.utc).isoformat(),
        "buckets": cleaned,
    }]
    from app.services.extraction.validation import summarize as _summarize
    r.validation_status = ValidationStatus.MANUAL_REVIEW if flags else ValidationStatus.VERIFIED
    r.llm_summary = "Edited — " + _summarize(cleaned, flags, r.month, r.year)
    await db.commit()
    await db.refresh(r)
    return to_out(r)


@router.post("/{record_id}/verify", response_model=TimesheetOut)
async def verify_record(record_id: str, db: AsyncSession = Depends(get_db)):
    """Manually mark a record as verified (clears manual_review)."""
    from app.models.timesheet_record import ValidationStatus
    r = (await db.execute(select(TimesheetRecord).where(TimesheetRecord.id == record_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Record not found")
    r.validation_status = ValidationStatus.VERIFIED
    r.hr_flags = []
    r.llm_summary = (r.llm_summary or "") + "  [Manually verified by reviewer.]"
    await db.commit()
    await db.refresh(r)
    return to_out(r)


@router.delete("/{record_id}")
async def delete_record(record_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(TimesheetRecord).where(TimesheetRecord.id == record_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Record not found")
    await db.delete(r)
    await db.commit()
    return {"deleted": record_id}


@router.get("/{record_id}/sources")
async def record_sources(record_id: str, db: AsyncSession = Depends(get_db)):
    """List the stored files (sheet, approval screenshot, result json) for this record."""
    from app.services import storage_provider as sp
    r = (await db.execute(select(TimesheetRecord).where(TimesheetRecord.id == record_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Record not found")
    if not r.storage_folder:
        return []
    try:
        parts = r.storage_folder.split("/")
        if len(parts) == 3:
            manager, emp, month = parts
        elif len(parts) == 2:
            manager, emp, month = "Unassigned", parts[0], parts[1]
        else:
            return []
        items = sp.get_storage_provider().list_items(manager, emp, month)
        return [{"name": i.name, "rel_path": i.rel_path, "content_type": i.content_type, "size": i.size}
                for i in items]
    except Exception:
        return []
