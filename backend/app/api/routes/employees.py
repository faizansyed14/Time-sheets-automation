"""Employee dashboard routes — employee matcher + roll-up status (green/yellow) per person."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.timesheets import to_out
from app.core.database import get_db
from app.models.employee import Employee
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord, ValidationStatus
from app.schemas import DashboardRow, TimesheetOut

router = APIRouter(prefix="/employees", tags=["employees"])


@router.get("", response_model=list[DashboardRow])
async def dashboard(
    year: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    One row per employee that has at least one record.
    status = yellow if ANY record needs review OR isn't approved yet; else green.
    """
    employees = (await db.execute(select(Employee))).scalars().all()
    emp_by_pk = {e.id: e for e in employees}

    recs = (await db.execute(select(TimesheetRecord))).scalars().all()

    grouped: dict[str, list[TimesheetRecord]] = {}
    for r in recs:
        key = r.matched_employee_pk or f"unmatched::{(r.employee_name or 'Unknown').lower()}"
        grouped.setdefault(key, []).append(r)

    out: list[DashboardRow] = []
    for key, items in grouped.items():
        years = sorted({r.year for r in items})
        scoped = [r for r in items if (year is None or r.year == year)]
        if year is not None and not scoped:
            continue
        consider = scoped or items

        needs_review = sum(1 for r in consider if r.validation_status == ValidationStatus.MANUAL_REVIEW)
        pending = sum(1 for r in consider if r.approval_status != ApprovalStatus.APPROVED)
        status = "yellow" if (needs_review > 0 or pending > 0) else "green"

        emp = emp_by_pk.get(key)
        sample = consider[0]
        out.append(DashboardRow(
            employee_pk=key if emp else None,
            employee_id=emp.employee_id if emp else sample.employee_id,
            employee_name=emp.name if emp else sample.employee_name,
            account_manager=emp.account_manager if emp else sample.account_manager,
            dco_number=emp.dco_number if emp else sample.dco_number,
            status=status,
            record_count=len(consider),
            needs_review_count=needs_review,
            pending_approval_count=pending,
            years=years,
        ))

    out.sort(key=lambda d: (d.status != "yellow", (d.employee_name or "").lower()))
    return out


@router.get("/{employee_pk}/records", response_model=list[TimesheetOut])
async def employee_records(
    employee_pk: str,
    year: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(TimesheetRecord)
    if employee_pk.startswith("unmatched::"):
        name = employee_pk.split("::", 1)[1]
        rows = (await db.execute(stmt)).scalars().all()
        rows = [r for r in rows if (r.employee_name or "").lower() == name]
    else:
        rows = (await db.execute(stmt.where(TimesheetRecord.matched_employee_pk == employee_pk))).scalars().all()
    if year:
        rows = [r for r in rows if r.year == year]
    rows.sort(key=lambda r: (r.year, r.month))
    return [to_out(r) for r in rows]
