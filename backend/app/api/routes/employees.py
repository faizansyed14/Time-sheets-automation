"""Employee dashboard routes — employee matcher + roll-up status (green/yellow)
per person, plus monthly submission coverage (who hasn't submitted yet)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.timesheets import to_out
from app.core import datacache
from app.core.database import get_db
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord, ValidationStatus
from app.schemas import DashboardRow, DashboardSummary, TimesheetOut
from app.services.pipeline.coverage import received_subq as _received_subq, staged_employee_pk

router = APIRouter(prefix="/employees", tags=["employees"])


@router.get("/coverage", response_model=DashboardSummary)
async def coverage(
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
    q: str | None = Query(default=None, description="search name / ID / manager (whole matcher)"),
    location: str | None = Query(default=None, description="DXB | AUH"),
    status: str | None = Query(
        default=None,
        description=(
            "submitted | missing | awaiting_review | needs_review | approved | "
            "not_approved | pending_approval"
        ),
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

    # Distinct employee PKs the pipeline positively identified from an EMAILED
    # sheet for the focus month/year — however far that item got (still
    # awaiting accept, already filed, or later flagged). This is "sent
    # something we could read the identity + period from", independent of
    # whether a reviewer has accepted it into a TimesheetRecord yet — see
    # services/pipeline/coverage.py (shared with the timesheet export so the
    # two counts never drift apart). Staging keeps this key through Accept
    # too, so a filed item still matches it, making `submitted_subq` a proper
    # SUBSET of this one.
    received_subq = _received_subq(focus_month, focus_year)
    staged_pk = staged_employee_pk()

    # ---- global headline counts (cached per focus month; busted on writes) ----
    # Inactive employees are excluded from every headline count and from the
    # "missing" roll-up — they're kept in the matcher (records/vault files
    # intact) but no longer expected to submit.
    async def _aggregates() -> dict:
        total = (await db.execute(
            select(func.count()).select_from(Employee).where(Employee.active.is_(True)))).scalar_one()
        submitted = (await db.execute(
            select(func.count()).select_from(
                select(Employee.id).where(Employee.active.is_(True), Employee.id.in_(submitted_subq)).subquery()
            ))).scalar_one()
        received = (await db.execute(
            select(func.count()).select_from(
                select(Employee.id).where(Employee.active.is_(True), Employee.id.in_(received_subq)).subquery()
            ))).scalar_one()
        # Received but not yet a filed record — sitting in Compare & Fix /
        # Activity awaiting a human Accept (or mid-reprocessing).
        awaiting = (await db.execute(
            select(func.count()).select_from(
                select(Employee.id).where(
                    Employee.active.is_(True),
                    Employee.id.in_(received_subq),
                    Employee.id.not_in(submitted_subq),
                ).subquery()
            ))).scalar_one()
        # Missing = NOT submitted AND NOT received. Checked against BOTH sets
        # explicitly rather than assuming submitted ⊆ received — a record can
        # be filed via a path that never produces a "staged.employee_pk"
        # PipelineFile (pure manual entry, source_kind="manual", or any
        # resolve/accept flow that doesn't preserve that JSON key), so an
        # employee can be fully submitted without ever showing up in
        # received_subq. Deriving missing as total-received would then wrongly
        # count them as missing even though submitted_subq already has them.
        missing = (await db.execute(
            select(func.count()).select_from(
                select(Employee.id).where(
                    Employee.active.is_(True),
                    Employee.id.not_in(submitted_subq),
                    Employee.id.not_in(received_subq),
                ).subquery()
            ))).scalar_one()
        nrev = (await db.execute(
            select(func.count(func.distinct(TimesheetRecord.matched_employee_pk)))
            .select_from(TimesheetRecord).join(Employee, Employee.id == TimesheetRecord.matched_employee_pk)
            .where(TimesheetRecord.year == focus_year,
                   TimesheetRecord.validation_status == ValidationStatus.MANUAL_REVIEW,
                   TimesheetRecord.matched_employee_pk.is_not(None),
                   Employee.active.is_(True)))).scalar_one()
        pend = (await db.execute(
            select(func.count(func.distinct(TimesheetRecord.matched_employee_pk)))
            .select_from(TimesheetRecord).join(Employee, Employee.id == TimesheetRecord.matched_employee_pk)
            .where(TimesheetRecord.year == focus_year,
                   TimesheetRecord.approval_status != ApprovalStatus.APPROVED,
                   TimesheetRecord.matched_employee_pk.is_not(None),
                   Employee.active.is_(True)))).scalar_one()
        # Sample for the KPI tooltip — same NOT-submitted-AND-NOT-received test as `missing` above.
        sample = (await db.execute(
            select(Employee.name).where(
                Employee.active.is_(True),
                Employee.id.not_in(submitted_subq),
                Employee.id.not_in(received_subq),
            ).order_by(Employee.name).limit(50))).scalars().all()
        return {"total_employees": total, "submitted_this_month": submitted,
                "received_this_month": received, "awaiting_review_this_month": awaiting,
                "missing_this_month": missing,
                "needs_review": nrev, "pending_approval": pend, "missing_sample": list(sample)}

    agg = await datacache.get_or_set(
        datacache.NS_COVERAGE, f"agg:{focus_year}-{focus_month}", datacache.TTL_COVERAGE, _aggregates)
    total_employees = agg["total_employees"]
    submitted_this_month = agg["submitted_this_month"]
    received_this_month = agg["received_this_month"]
    awaiting_review_this_month = agg["awaiting_review_this_month"]
    # total = missing + submitted + awaiting_review, exactly — each computed
    # as its own explicit disjoint condition (see _aggregates above), not
    # derived from `received` alone.
    missing_this_month = agg["missing_this_month"]
    needs_review = agg["needs_review"]
    pending_approval = agg["pending_approval"]
    missing_sample = agg["missing_sample"]

    def _pct(n: int) -> float:
        return round(n / total_employees * 100, 1) if total_employees else 0.0

    submitted_pct = _pct(submitted_this_month)
    missing_pct = _pct(missing_this_month)
    awaiting_review_pct = _pct(awaiting_review_this_month)

    # ---- filtered + paginated employee rows ----
    emp_q = select(Employee).where(Employee.active.is_(True))
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
        emp_q = emp_q.where(Employee.id.not_in(submitted_subq), Employee.id.not_in(received_subq))

    # Server-side status filter (applies across the WHOLE matcher, not just the
    # loaded page). Scoped to the focus year so it tracks the dashboard KPIs.
    if status:
        s = status.strip().lower()
        if s == "submitted":
            emp_q = emp_q.where(Employee.id.in_(submitted_subq))
        elif s == "missing":
            emp_q = emp_q.where(Employee.id.not_in(submitted_subq), Employee.id.not_in(received_subq))
        elif s == "awaiting_review":
            emp_q = emp_q.where(Employee.id.in_(received_subq), Employee.id.not_in(submitted_subq))
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

    # Same page's "received this month" set (see received_subq above) — lets
    # each row distinguish "awaiting review" (sent, not yet filed) from a true
    # "missing" without a second round trip per row.
    page_received_pks: set[str] = set()
    if page_pks:
        page_received_pks = set((await db.execute(
            select(staged_pk).where(
                PipelineFile.source_kind == "email",
                PipelineFile.month == focus_month,
                PipelineFile.year == focus_year,
                staged_pk.in_(page_pks),
            ).distinct()
        )).scalars().all())

    rows: list[DashboardRow] = []
    for e in page_emps:
        items = by_pk.get(e.id, [])
        scoped = [r for r in items if r.year == focus_year]
        focus_rec = next((r for r in scoped if r.month == focus_month), None)
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
            focus_record_id=focus_rec.id if focus_rec else None,
            focus_validation_status=focus_rec.validation_status if focus_rec else None,
            focus_approval_status=focus_rec.approval_status if focus_rec else None,
            awaiting_review_this_month=bool(e.id in page_received_pks and not focus_rec),
        ))

    # missing_sample (for the KPI tooltip) is part of the cached aggregates above.
    return DashboardSummary(
        year=focus_year, month=focus_month,
        total_employees=total_employees,
        submitted_this_month=submitted_this_month,
        missing_this_month=missing_this_month,
        awaiting_review_this_month=awaiting_review_this_month,
        submitted_pct=submitted_pct, missing_pct=missing_pct, awaiting_review_pct=awaiting_review_pct,
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
