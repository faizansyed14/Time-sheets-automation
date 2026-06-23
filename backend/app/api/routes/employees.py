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


@router.get("/coverage", response_model=DashboardSummary)
async def coverage(
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
    q: str | None = Query(default=None, description="search name / ID / manager (whole matcher)"),
    location: str | None = Query(default=None, description="DXB | AUH"),
    status: str | None = Query(
        default=None,
        description="submitted | missing | needs_review | approved | not_approved | pending_approval",
    ),
    only_missing: bool = Query(default=False, description="only employees missing the focus month"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Submission coverage for a focus month. Headline counts are computed with
    cheap aggregate queries over the WHOLE dataset; the per-employee rows are
    filtered/searched in SQL across the whole matcher and returned one page (200)
    at a time for infinite scroll — so the status dropdown and search reflect ALL
    data, never just the current page."""
    now = datetime.now(timezone.utc)
    focus_year = year or now.year
    focus_month = month or (now.month if focus_year == now.year else 12)

    # ---- global headline counts (aggregates, not full-table scans) ----
    total_employees = (await db.execute(select(func.count()).select_from(Employee))).scalar_one()

    def _pk_subq(*conds):
        """Distinct matched employee PKs whose records satisfy `conds`."""
        return (
            select(TimesheetRecord.matched_employee_pk)
            .where(TimesheetRecord.matched_employee_pk.is_not(None), *conds)
            .distinct()
        )

    submitted_subq = _pk_subq(
        TimesheetRecord.year == focus_year, TimesheetRecord.month == focus_month
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

    # Server-side status filter (applies across the WHOLE matcher, not just the
    # loaded page). Scoped to the focus year so it tracks the dashboard KPIs.
    if status:
        s = status.strip().lower()
        if s == "submitted":
            emp_q = emp_q.where(Employee.id.in_(submitted_subq))
        elif s == "missing":
            emp_q = emp_q.where(Employee.id.not_in(submitted_subq))
        elif s == "needs_review":
            emp_q = emp_q.where(Employee.id.in_(_pk_subq(
                TimesheetRecord.year == focus_year,
                TimesheetRecord.validation_status == ValidationStatus.MANUAL_REVIEW)))
        elif s == "approved":
            emp_q = emp_q.where(Employee.id.in_(_pk_subq(
                TimesheetRecord.year == focus_year,
                TimesheetRecord.approval_status == ApprovalStatus.APPROVED)))
        elif s == "not_approved":
            emp_q = emp_q.where(Employee.id.in_(_pk_subq(
                TimesheetRecord.year == focus_year,
                TimesheetRecord.approval_status == ApprovalStatus.NOT_APPROVED)))
        elif s == "pending_approval":
            emp_q = emp_q.where(Employee.id.in_(_pk_subq(
                TimesheetRecord.year == focus_year,
                TimesheetRecord.approval_status == ApprovalStatus.PENDING)))

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
