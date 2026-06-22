"""Employee dashboard routes — employee matcher + roll-up status (green/yellow)
per person, plus monthly submission coverage (who hasn't submitted yet)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.timesheets import to_out
from app.core.database import get_db
from app.models.employee import Employee
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord, ValidationStatus
from app.schemas import DashboardRow, DashboardSummary, TimesheetOut

router = APIRouter(prefix="/employees", tags=["employees"])


def _build_rows(employees, recs, year: int | None) -> list[DashboardRow]:
    """One row per matcher employee (even with zero records) + any unmatched
    groups, carrying the months they submitted in the focus year."""
    emp_by_pk = {e.id: e for e in employees}
    grouped: dict[str, list[TimesheetRecord]] = {}
    for r in recs:
        key = r.matched_employee_pk or f"unmatched::{(r.employee_name or 'Unknown').lower()}"
        grouped.setdefault(key, []).append(r)

    rows: list[DashboardRow] = []

    # 1) every employee in the matcher list (so we can spot who is MISSING)
    for emp in employees:
        items = grouped.get(emp.id, [])
        years = sorted({r.year for r in items})
        scoped = [r for r in items if (year is None or r.year == year)]
        consider = scoped or items
        needs_review = sum(1 for r in scoped if r.validation_status == ValidationStatus.MANUAL_REVIEW)
        pending = sum(1 for r in scoped if r.approval_status != ApprovalStatus.APPROVED)
        submitted_months = sorted({r.month for r in scoped})
        status = "yellow" if (needs_review > 0 or pending > 0) else "green"
        rows.append(DashboardRow(
            employee_pk=emp.id,
            employee_id=emp.employee_id,
            employee_name=emp.name,
            account_manager=emp.account_manager,
            dco_number=emp.dco_number,
            location=emp.location,
            status=status,
            record_count=len(scoped),
            needs_review_count=needs_review,
            pending_approval_count=pending,
            years=years,
            submitted_months=submitted_months,
            in_matcher=True,
            has_records=bool(consider),
        ))

    # 2) records whose employee isn't in the matcher (unmatched groups)
    for key, items in grouped.items():
        if not key.startswith("unmatched::"):
            continue
        scoped = [r for r in items if (year is None or r.year == year)]
        if year is not None and not scoped:
            continue
        consider = scoped or items
        needs_review = sum(1 for r in consider if r.validation_status == ValidationStatus.MANUAL_REVIEW)
        pending = sum(1 for r in consider if r.approval_status != ApprovalStatus.APPROVED)
        sample = consider[0]
        rows.append(DashboardRow(
            employee_pk=key,
            employee_id=sample.employee_id,
            employee_name=sample.employee_name,
            account_manager=sample.account_manager,
            dco_number=sample.dco_number,
            location=None,
            status="yellow" if (needs_review or pending) else "green",
            record_count=len(consider),
            needs_review_count=needs_review,
            pending_approval_count=pending,
            years=sorted({r.year for r in items}),
            submitted_months=sorted({r.month for r in scoped}),
            in_matcher=False,
            has_records=True,
        ))

    rows.sort(key=lambda d: (d.status != "yellow", not d.has_records, (d.employee_name or "").lower()))
    return rows


@router.get("", response_model=list[DashboardRow])
async def dashboard(
    year: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """One row per employee. Includes matcher employees with no records yet so
    the UI can show who is missing for a given month."""
    employees = (await db.execute(select(Employee))).scalars().all()
    recs = (await db.execute(select(TimesheetRecord))).scalars().all()
    return _build_rows(employees, recs, year)


@router.get("/coverage", response_model=DashboardSummary)
async def coverage(
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
    q: str | None = Query(default=None, description="search name / ID / manager (whole matcher)"),
    location: str | None = Query(default=None, description="DXB | AUH"),
    only_missing: bool = Query(default=False, description="only employees missing the focus month"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Submission coverage for a focus month. Headline counts are computed with
    cheap aggregate queries over the WHOLE dataset; the per-employee rows are
    searched in SQL and returned one page (200) at a time for infinite scroll."""
    now = datetime.now(timezone.utc)
    focus_year = year or now.year
    focus_month = month or (now.month if focus_year == now.year else 12)

    # ---- global headline counts (aggregates, not full-table scans) ----
    total_employees = (await db.execute(select(func.count()).select_from(Employee))).scalar_one()

    submitted_subq = (
        select(TimesheetRecord.matched_employee_pk)
        .where(TimesheetRecord.year == focus_year, TimesheetRecord.month == focus_month,
               TimesheetRecord.matched_employee_pk.is_not(None))
        .distinct()
    )
    submitted_this_month = (
        await db.execute(select(func.count()).select_from(submitted_subq.subquery()))
    ).scalar_one()
    missing_this_month = max(0, total_employees - submitted_this_month)

    needs_review = (await db.execute(
        select(func.count(func.distinct(TimesheetRecord.matched_employee_pk)))
        .where(TimesheetRecord.year == focus_year,
               TimesheetRecord.validation_status == ValidationStatus.MANUAL_REVIEW,
               TimesheetRecord.matched_employee_pk.is_not(None))
    )).scalar_one()
    pending_approval = (await db.execute(
        select(func.count(func.distinct(TimesheetRecord.matched_employee_pk)))
        .where(TimesheetRecord.year == focus_year,
               TimesheetRecord.approval_status != ApprovalStatus.APPROVED,
               TimesheetRecord.matched_employee_pk.is_not(None))
    )).scalar_one()

    # ---- filtered + paginated employee rows ----
    emp_q = select(Employee)
    if location:
        emp_q = emp_q.where(Employee.location == location)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        emp_q = emp_q.where(or_(
            func.lower(Employee.name).like(like),
            func.lower(Employee.employee_id).like(like),
            func.lower(func.coalesce(Employee.account_manager, "")).like(like),
            func.lower(func.coalesce(Employee.location, "")).like(like),
        ))
    if only_missing:
        emp_q = emp_q.where(Employee.id.not_in(submitted_subq))

    filtered_total = (await db.execute(select(func.count()).select_from(emp_q.subquery()))).scalar_one()
    page_emps = (await db.execute(
        emp_q.order_by(Employee.name).limit(limit).offset(offset)
    )).scalars().all()

    # records for just this page's employees (bounded by `limit`) — cheap
    page_pks = [e.id for e in page_emps]
    by_pk: dict[str, list[TimesheetRecord]] = {}
    if page_pks:
        precs = (await db.execute(
            select(TimesheetRecord).where(TimesheetRecord.matched_employee_pk.in_(page_pks))
        )).scalars().all()
        for r in precs:
            by_pk.setdefault(r.matched_employee_pk, []).append(r)

    rows: list[DashboardRow] = []
    for e in page_emps:
        items = by_pk.get(e.id, [])
        scoped = [r for r in items if r.year == focus_year]
        nrev = sum(1 for r in scoped if r.validation_status == ValidationStatus.MANUAL_REVIEW)
        pend = sum(1 for r in scoped if r.approval_status != ApprovalStatus.APPROVED)
        rows.append(DashboardRow(
            employee_pk=e.id, employee_id=e.employee_id, employee_name=e.name,
            account_manager=e.account_manager, dco_number=e.dco_number, location=e.location,
            status="yellow" if (nrev or pend) else "green",
            record_count=len(scoped), needs_review_count=nrev, pending_approval_count=pend,
            years=sorted({r.year for r in items}),
            submitted_months=sorted({r.month for r in scoped}),
            in_matcher=True, has_records=bool(items),
        ))

    # a small sample of who is missing the focus month (for the KPI tooltip)
    missing_sample = (await db.execute(
        select(Employee.name).where(Employee.id.not_in(submitted_subq)).order_by(Employee.name).limit(50)
    )).scalars().all()

    return DashboardSummary(
        year=focus_year, month=focus_month,
        total_employees=total_employees,
        submitted_this_month=submitted_this_month,
        missing_this_month=missing_this_month,
        needs_review=needs_review, pending_approval=pending_approval,
        missing_employees=list(missing_sample),
        rows=rows, filtered_total=filtered_total, limit=limit, offset=offset,
        has_more=offset + len(rows) < filtered_total,
    )


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
