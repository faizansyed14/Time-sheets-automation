"""
Timesheet chat tools — the *only* way the agentic chat can touch the database.

Every capability the assistant has is a function here, executed against the
SQLAlchemy models with the same validation the UI uses. There is no raw SQL and
no free-form execution, so the agent cannot be coerced into doing anything
outside the timesheet domain (read employees / timesheets / leaves, and edit
leave buckets). Record deletion is deliberately NOT offered.

Each tool returns a JSON-serialisable dict. Write tools also return a `change`
block (before → after) so the UI can show exactly what was modified.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord, ValidationStatus

# User-facing leave name (and common aliases) -> TimesheetRecord field.
LEAVE_FIELDS: dict[str, str] = {
    "annual": "annual_leave_dates",
    "annual_leave": "annual_leave_dates",
    "al": "annual_leave_dates",
    "vacation": "annual_leave_dates",
    "remote": "remote_work_dates",
    "remote_work": "remote_work_dates",
    "wfh": "remote_work_dates",
    "work_from_home": "remote_work_dates",
    "sick": "sick_leave_dates",
    "sick_leave": "sick_leave_dates",
    "sl": "sick_leave_dates",
    "maternity": "maternity_leave_dates",
    "maternity_leave": "maternity_leave_dates",
    "ml": "maternity_leave_dates",
    "unpaid": "unpaid_leave_dates",
    "unpaid_leave": "unpaid_leave_dates",
    "lop": "unpaid_leave_dates",
    "absent": "absent_dates",
    "absence": "absent_dates",
    "public_holiday": "public_holiday_dates",
    "public": "public_holiday_dates",
    "holiday": "public_holiday_dates",
    "ph": "public_holiday_dates",
}

_FIELD_LABELS = {
    "annual_leave_dates": "Annual leave",
    "remote_work_dates": "Remote work (WFH)",
    "sick_leave_dates": "Sick leave",
    "maternity_leave_dates": "Maternity leave",
    "unpaid_leave_dates": "Unpaid leave (LOP)",
    "absent_dates": "Absent",
    "public_holiday_dates": "Public holiday",
}

_MONTHS = ["", "January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]


def _resolve_leave_field(leave_type: str | None) -> str | None:
    if not leave_type:
        return None
    key = leave_type.strip().lower().replace(" ", "_").replace("-", "_")
    if key in LEAVE_FIELDS:
        return LEAVE_FIELDS[key]
    # tolerate "annual leaves", "sick days", trailing plurals
    key = key.rstrip("s")
    return LEAVE_FIELDS.get(key)


def _emp_summary(e: Employee) -> dict[str, Any]:
    return {
        "employee_pk": e.id,
        "employee_id": e.employee_id,
        "name": e.name,
        "location": e.location,
        "account_manager": e.account_manager,
        "project": e.project,
    }


def _leave_counts(r: TimesheetRecord) -> dict[str, int]:
    return {label: len(getattr(r, field) or [])
            for field, label in _FIELD_LABELS.items()}


def _record_summary(r: TimesheetRecord) -> dict[str, Any]:
    return {
        "record_id": r.id,
        "employee_id": r.employee_id,
        "employee_name": r.employee_name,
        "month": r.month,
        "year": r.year,
        "month_name": _MONTHS[r.month] if 0 < r.month < 13 else str(r.month),
        "validation_status": r.validation_status,
        "approval_status": r.approval_status,
        "leaves": _leave_counts(r),
        "leave_dates": {label: (getattr(r, field) or [])
                        for field, label in _FIELD_LABELS.items()},
    }


# --------------------------------------------------------------------------- #
# Employee resolution
# --------------------------------------------------------------------------- #
async def _match_employees(db: AsyncSession, query: str) -> list[Employee]:
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q.lower()}%"
    stmt = select(Employee).where(or_(
        func.lower(Employee.name).like(like),
        func.lower(Employee.employee_id).like(like),
    )).order_by(Employee.name).limit(25)
    rows = list((await db.execute(stmt)).scalars().all())
    # Prefer an exact (case-insensitive) name/id hit if present.
    exact = [e for e in rows if e.name.lower() == q.lower() or e.employee_id.lower() == q.lower()]
    return exact or rows


async def find_employees(db: AsyncSession, query: str) -> dict[str, Any]:
    """Search the employee matcher by name or employee id."""
    rows = await _match_employees(db, query)
    return {"query": query, "count": len(rows),
            "employees": [_emp_summary(e) for e in rows]}


async def _resolve_one(db: AsyncSession, employee: str) -> tuple[Employee | None, list[Employee]]:
    rows = await _match_employees(db, employee)
    if len(rows) == 1:
        return rows[0], rows
    return None, rows


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #
async def get_employee_timesheets(
    db: AsyncSession, employee: str, month: int | None = None, year: int | None = None,
) -> dict[str, Any]:
    """List an employee's timesheet records (optionally for one month/year)."""
    one, rows = await _resolve_one(db, employee)
    if not one:
        return {"status": "ambiguous" if rows else "not_found",
                "matches": [_emp_summary(e) for e in rows]}
    stmt = select(TimesheetRecord).where(
        or_(TimesheetRecord.matched_employee_pk == one.id,
            func.lower(TimesheetRecord.employee_name) == one.name.lower()))
    if month:
        stmt = stmt.where(TimesheetRecord.month == month)
    if year:
        stmt = stmt.where(TimesheetRecord.year == year)
    recs = list((await db.execute(stmt.order_by(
        TimesheetRecord.year.desc(), TimesheetRecord.month.desc()))).scalars().all())
    return {"status": "ok", "employee": _emp_summary(one),
            "record_count": len(recs), "records": [_record_summary(r) for r in recs]}


async def count_leaves(
    db: AsyncSession, employee: str, leave_type: str,
    month: int | None = None, year: int | None = None,
) -> dict[str, Any]:
    """Count an employee's leaves of a given type, optionally scoped to a month/year."""
    field = _resolve_leave_field(leave_type)
    if not field:
        return {"status": "unknown_leave_type", "leave_type": leave_type,
                "valid_types": sorted(set(_FIELD_LABELS.values()))}
    ts = await get_employee_timesheets(db, employee, month, year)
    if ts["status"] != "ok":
        return ts
    label = _FIELD_LABELS[field]
    total = 0
    per_record = []
    for r in ts["records"]:
        n = r["leaves"].get(label, 0)
        total += n
        per_record.append({"month": r["month"], "year": r["year"],
                           "month_name": r["month_name"], "count": n,
                           "dates": r["leave_dates"].get(label, [])})
    return {"status": "ok", "employee": ts["employee"], "leave_type": label,
            "total": total, "per_record": per_record}


async def check_submission(
    db: AsyncSession, employee: str, month: int, year: int,
) -> dict[str, Any]:
    """Has the employee submitted a timesheet for the given month/year?"""
    ts = await get_employee_timesheets(db, employee, month, year)
    if ts["status"] != "ok":
        return ts
    submitted = ts["record_count"] > 0
    return {"status": "ok", "employee": ts["employee"], "month": month, "year": year,
            "month_name": _MONTHS[month] if 0 < month < 13 else str(month),
            "submitted": submitted,
            "records": ts["records"]}


async def employee_overview(
    db: AsyncSession, employee: str, year: int | None = None,
) -> dict[str, Any]:
    """Submission & manager-approval overview for one employee across every month.

    Deterministic roll-up over the employee's timesheet records: which months
    were submitted, which were manager-approved (vs pending / not approved),
    machine validation status and the total leave days per month. Use this to
    answer 'how many months did X submit / get approved, and for which months'.
    Optionally scope to a single `year`.
    """
    ts = await get_employee_timesheets(db, employee, None, year)
    if ts["status"] != "ok":
        return ts

    months: list[dict[str, Any]] = []
    approved = pending = not_approved = 0
    for r in ts["records"]:
        appr = r["approval_status"]
        if appr == "approved":
            approved += 1
        elif appr == "not_approved":
            not_approved += 1
        else:
            pending += 1
        months.append({
            "month": r["month"],
            "year": r["year"],
            "month_name": r["month_name"],
            "submitted": True,
            "approval_status": appr,
            "validation_status": r["validation_status"],
            "total_leaves": sum(r["leaves"].values()),
            "leaves": r["leaves"],
        })

    return {
        "status": "ok",
        "employee": ts["employee"],
        "year_filter": year,
        "months_submitted": len(months),
        "months_approved": approved,
        "months_pending_approval": pending,
        "months_not_approved": not_approved,
        "submitted_months": [
            f"{m['month_name']} {m['year']}" for m in months
        ],
        "approved_months": [
            f"{m['month_name']} {m['year']}" for m in months
            if m["approval_status"] == "approved"
        ],
        "months": months,
    }


async def list_submitted(db: AsyncSession, month: int, year: int) -> dict[str, Any]:
    """List employees who DID submit a timesheet for the given month/year, with
    each one's approval status. Use for 'who submitted / who sent their sheet'.
    (This is the opposite of list_missing — never infer one from the other.)"""
    recs = list((await db.execute(select(TimesheetRecord).where(
        TimesheetRecord.month == month, TimesheetRecord.year == year).order_by(
        TimesheetRecord.employee_name))).scalars().all())
    submitted = [{
        "record_id": r.id,
        "employee_name": r.employee_name,
        "employee_id": r.employee_id,
        "approval_status": r.approval_status,
        "validation_status": r.validation_status,
        "total_leaves": sum(len(getattr(r, f) or []) for f in _FIELD_LABELS),
    } for r in recs]
    return {"status": "ok", "month": month, "year": year,
            "month_name": _MONTHS[month] if 0 < month < 13 else str(month),
            "count": len(submitted), "submitted": submitted}


async def list_missing(db: AsyncSession, month: int, year: int) -> dict[str, Any]:
    """List employees with NO timesheet record for the given month/year."""
    emps = list((await db.execute(select(Employee).order_by(Employee.name))).scalars().all())
    recs = list((await db.execute(select(TimesheetRecord).where(
        TimesheetRecord.month == month, TimesheetRecord.year == year))).scalars().all())
    submitted_pks = {r.matched_employee_pk for r in recs if r.matched_employee_pk}
    submitted_names = {(r.employee_name or "").lower() for r in recs}
    missing = [_emp_summary(e) for e in emps
               if e.id not in submitted_pks and e.name.lower() not in submitted_names]
    return {"status": "ok", "month": month, "year": year,
            "month_name": _MONTHS[month] if 0 < month < 13 else str(month),
            "total_employees": len(emps), "submitted": len(emps) - len(missing),
            "missing_count": len(missing), "missing": missing}


# --------------------------------------------------------------------------- #
# Write tool (CRUD on leaves — never deletes a record)
# --------------------------------------------------------------------------- #
def _normalize_dates(dates: list[str], month: int, year: int) -> tuple[list[str], list[str]]:
    """Accept ISO dates or bare day numbers; return (iso_dates, rejected)."""
    out: list[str] = []
    rejected: list[str] = []
    last_day = calendar.monthrange(year, month)[1]
    for raw in dates or []:
        s = str(raw).strip()
        if not s:
            continue
        iso: str | None = None
        try:
            iso = dt.date.fromisoformat(s).isoformat()
        except ValueError:
            if s.isdigit() and 1 <= int(s) <= last_day:
                iso = dt.date(year, month, int(s)).isoformat()
        if iso:
            out.append(iso)
        else:
            rejected.append(s)
    return out, rejected


async def update_leaves(
    db: AsyncSession,
    employee: str,
    month: int,
    year: int,
    leave_type: str,
    mode: str = "add",
    dates: list[str] | None = None,
) -> dict[str, Any]:
    """Add / set / clear a leave bucket for one employee-month.

    mode: "add" (append dates), "set" (replace the bucket with dates),
          "clear" (empty the bucket — clears leaves, never the record).
    Re-runs validation and reports the before → after change.
    """
    from app.services.extraction.validation import summarize as _summarize
    from app.services.extraction.validation import validate as _validate

    field = _resolve_leave_field(leave_type)
    if not field:
        return {"status": "unknown_leave_type", "leave_type": leave_type,
                "valid_types": sorted(set(_FIELD_LABELS.values()))}
    mode = (mode or "add").lower()
    if mode not in ("add", "set", "clear"):
        return {"status": "bad_mode", "mode": mode, "valid_modes": ["add", "set", "clear"]}

    one, rows = await _resolve_one(db, employee)
    if not one:
        return {"status": "ambiguous" if rows else "not_found",
                "matches": [_emp_summary(e) for e in rows]}

    rec = (await db.execute(select(TimesheetRecord).where(
        or_(TimesheetRecord.matched_employee_pk == one.id,
            func.lower(TimesheetRecord.employee_name) == one.name.lower()),
        TimesheetRecord.month == month, TimesheetRecord.year == year,
    ).limit(1))).scalar_one_or_none()
    if not rec:
        return {"status": "no_record", "employee": _emp_summary(one),
                "month": month, "year": year,
                "message": f"{one.name} has no timesheet for {_MONTHS[month]} {year} to edit."}

    label = _FIELD_LABELS[field]
    before = list(getattr(rec, field) or [])

    new_dates, rejected = ([], [])
    if mode != "clear":
        new_dates, rejected = _normalize_dates(dates or [], month, year)
        if not new_dates:
            return {"status": "no_valid_dates", "rejected": rejected,
                    "message": "No valid dates were provided for this edit."}

    if mode == "clear":
        target = []
    elif mode == "set":
        target = new_dates
    else:  # add
        target = sorted(set(before) | set(new_dates))
    setattr(rec, field, target)

    # Re-validate the whole month and refresh status/summary (same as the UI edit).
    buckets = {
        "annual": rec.annual_leave_dates or [], "remote": rec.remote_work_dates or [],
        "sick": rec.sick_leave_dates or [], "maternity": rec.maternity_leave_dates or [],
        "unpaid": rec.unpaid_leave_dates or [],
        "absent": rec.absent_dates or [], "public_holiday": rec.public_holiday_dates or [],
    }
    cleaned, flags = _validate(buckets, rec.month, rec.year)
    rec.annual_leave_dates = cleaned["annual"]
    rec.remote_work_dates = cleaned["remote"]
    rec.sick_leave_dates = cleaned["sick"]
    rec.maternity_leave_dates = cleaned["maternity"]
    rec.unpaid_leave_dates = cleaned["unpaid"]
    rec.absent_dates = cleaned["absent"]
    rec.public_holiday_dates = cleaned["public_holiday"]
    rec.hr_flags = flags
    rec.validation_status = ValidationStatus.MANUAL_REVIEW if flags else ValidationStatus.VERIFIED
    rec.llm_summary = "Chat edit — " + _summarize(cleaned, flags, rec.month, rec.year)

    after = list(getattr(rec, field) or [])
    await db.commit()
    await db.refresh(rec)

    from app.core import datacache
    await datacache.bust_coverage()

    return {
        "status": "ok",
        "employee": _emp_summary(one),
        "change": {
            "record_id": rec.id,
            "employee_name": rec.employee_name,
            "month": rec.month, "year": rec.year,
            "month_name": _MONTHS[rec.month] if 0 < rec.month < 13 else str(rec.month),
            "leave_type": label,
            "action": mode,
            "before": before,
            "after": after,
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
        },
        "rejected_dates": rejected,
        "validation_status": rec.validation_status,
        "summary": rec.llm_summary,
    }


# --------------------------------------------------------------------------- #
# Approval tool — set a record's MANAGER-approval verdict (never deletes).
# --------------------------------------------------------------------------- #
async def set_approval(
    db: AsyncSession, employee: str, month: int, year: int,
    approved: bool, detail: str | None = None,
) -> dict[str, Any]:
    """Mark an employee-month timesheet as manager-approved or not-approved.

    This only flips the approval verdict on an existing record — it never
    deletes or alters leave data. Use when the user says "approve X's timesheet
    for May" or "mark it not approved".
    """
    one, rows = await _resolve_one(db, employee)
    if not one:
        return {"status": "ambiguous" if rows else "not_found",
                "matches": [_emp_summary(e) for e in rows]}
    rec = (await db.execute(select(TimesheetRecord).where(
        or_(TimesheetRecord.matched_employee_pk == one.id,
            func.lower(TimesheetRecord.employee_name) == one.name.lower()),
        TimesheetRecord.month == month, TimesheetRecord.year == year,
    ).limit(1))).scalar_one_or_none()
    if not rec:
        return {"status": "no_record", "employee": _emp_summary(one),
                "month": month, "year": year,
                "message": f"{one.name} has no timesheet for {_MONTHS[month]} {year} to approve."}
    before = rec.approval_status
    rec.approval_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.NOT_APPROVED
    if detail:
        rec.approval_detail = str(detail)[:500]
    await db.commit()
    await db.refresh(rec)
    from app.core import datacache
    await datacache.bust_coverage()
    return {
        "status": "ok",
        "employee": _emp_summary(one),
        "approval_change": {
            "record_id": rec.id,
            "employee_name": rec.employee_name,
            "month": rec.month, "year": rec.year,
            "month_name": _MONTHS[rec.month] if 0 < rec.month < 13 else str(rec.month),
            "before": before,
            "after": rec.approval_status,
        },
    }


# --------------------------------------------------------------------------- #
# Analytics / insight tools (org-wide, proactive)
# --------------------------------------------------------------------------- #
async def _all_records_for(db: AsyncSession, month: int, year: int) -> list[TimesheetRecord]:
    return list((await db.execute(select(TimesheetRecord).where(
        TimesheetRecord.month == month, TimesheetRecord.year == year))).scalars().all())


async def dashboard_summary(db: AsyncSession, month: int, year: int) -> dict[str, Any]:
    """Org-wide roll-up for a month: totals submitted / missing, pending manager
    approval, and records flagged for review. Use for 'how are we doing for
    May', status overviews, or as a proactive health check."""
    emps = list((await db.execute(select(Employee))).scalars().all())
    recs = await _all_records_for(db, month, year)
    submitted_pks = {r.matched_employee_pk for r in recs if r.matched_employee_pk}
    submitted_names = {(r.employee_name or "").lower() for r in recs}
    missing = [e for e in emps
               if e.id not in submitted_pks and e.name.lower() not in submitted_names]
    approved = [r for r in recs if r.approval_status == ApprovalStatus.APPROVED]
    # "Awaiting approval" = anything submitted but not yet approved (pending OR
    # not_approved — the pipeline files un-approved sheets as not_approved).
    awaiting = [r for r in recs if r.approval_status != ApprovalStatus.APPROVED]
    needs_review = [r for r in recs if r.validation_status == ValidationStatus.MANUAL_REVIEW]
    return {
        "status": "ok", "month": month, "year": year,
        "month_name": _MONTHS[month] if 0 < month < 13 else str(month),
        "total_employees": len(emps),
        "submitted": len(emps) - len(missing),
        "missing_count": len(missing),
        "approved_count": len(approved),
        "awaiting_approval_count": len(awaiting),
        "needs_review_count": len(needs_review),
        "missing": [_emp_summary(e) for e in missing[:50]],
        "awaiting_approval": [_record_summary(r) for r in awaiting[:50]],
        "needs_review": [_record_summary(r) for r in needs_review[:50]],
    }


async def pending_approvals(
    db: AsyncSession, month: int | None = None, year: int | None = None,
) -> dict[str, Any]:
    """List timesheets that are NOT YET manager-approved — i.e. still need
    sign-off. This is EVERYTHING whose approval status is not 'approved', which
    covers both 'pending' (no decision recorded) and 'not_approved' (filed
    without a detected approval). Use for 'what's pending / awaiting approval'
    or 'who still needs sign-off'. Optionally scope to a month/year."""
    stmt = select(TimesheetRecord).where(
        TimesheetRecord.approval_status != ApprovalStatus.APPROVED)
    if month:
        stmt = stmt.where(TimesheetRecord.month == month)
    if year:
        stmt = stmt.where(TimesheetRecord.year == year)
    recs = list((await db.execute(stmt.order_by(
        TimesheetRecord.year.desc(), TimesheetRecord.month.desc(),
        TimesheetRecord.employee_name))).scalars().all())
    return {"status": "ok", "month": month, "year": year,
            "count": len(recs), "records": [_record_summary(r) for r in recs[:100]]}


async def team_overview(
    db: AsyncSession, month: int, year: int,
    group_by: str = "account_manager",
) -> dict[str, Any]:
    """Group the month's submission/approval status by team (account_manager or
    location). Use for 'how is <manager>'s team doing' or 'break down May by
    location'."""
    field = "location" if str(group_by).lower().startswith("loc") else "account_manager"
    emps = list((await db.execute(select(Employee))).scalars().all())
    recs = await _all_records_for(db, month, year)
    rec_by_pk = {r.matched_employee_pk: r for r in recs if r.matched_employee_pk}
    rec_by_name = {(r.employee_name or "").lower(): r for r in recs}
    groups: dict[str, dict[str, Any]] = {}
    for e in emps:
        key = getattr(e, field) or "—"
        g = groups.setdefault(key, {"group": key, "total": 0, "submitted": 0,
                                    "approved": 0, "pending": 0, "missing": 0})
        g["total"] += 1
        r = rec_by_pk.get(e.id) or rec_by_name.get(e.name.lower())
        if r:
            g["submitted"] += 1
            if r.approval_status == ApprovalStatus.APPROVED:
                g["approved"] += 1
            elif r.approval_status == ApprovalStatus.PENDING:
                g["pending"] += 1
        else:
            g["missing"] += 1
    return {"status": "ok", "month": month, "year": year, "group_by": field,
            "month_name": _MONTHS[month] if 0 < month < 13 else str(month),
            "groups": sorted(groups.values(), key=lambda g: -g["missing"])}


async def compare_months(
    db: AsyncSession, employee: str,
    month_a: int, year_a: int, month_b: int, year_b: int,
) -> dict[str, Any]:
    """Compare one employee's leave totals between two months. Use for trend
    questions like 'did X take more sick leave in June than May'."""
    a = await get_employee_timesheets(db, employee, month_a, year_a)
    if a["status"] != "ok":
        return a
    b = await get_employee_timesheets(db, employee, month_b, year_b)

    def _totals(ts: dict) -> dict[str, int]:
        out = {label: 0 for label in _FIELD_LABELS.values()}
        for r in ts.get("records", []):
            for label, n in r["leaves"].items():
                out[label] += n
        return out

    ta, tb = _totals(a), _totals(b)
    deltas = {label: tb[label] - ta[label] for label in _FIELD_LABELS.values()}
    return {"status": "ok", "employee": a["employee"],
            "period_a": {"month": month_a, "year": year_a,
                         "month_name": _MONTHS[month_a], "totals": ta},
            "period_b": {"month": month_b, "year": year_b,
                         "month_name": _MONTHS[month_b], "totals": tb},
            "deltas": {k: v for k, v in deltas.items() if v}}


async def find_anomalies(db: AsyncSession, month: int, year: int) -> dict[str, Any]:
    """Flag records whose leave looks unusual for THIS month — high sick/absent
    counts or records still flagged for review. Use proactively for 'anything
    that needs my attention this month'."""
    recs = await _all_records_for(db, month, year)
    anomalies: list[dict[str, Any]] = []
    for r in recs:
        reasons = []
        sick = len(r.sick_leave_dates or [])
        absent = len(r.absent_dates or [])
        unpaid = len(r.unpaid_leave_dates or [])
        if sick >= 5:
            reasons.append(f"{sick} sick days")
        if absent >= 3:
            reasons.append(f"{absent} absent days")
        if unpaid >= 3:
            reasons.append(f"{unpaid} unpaid days")
        if r.validation_status == ValidationStatus.MANUAL_REVIEW:
            reasons.append("flagged for review")
        if reasons:
            anomalies.append({**_record_summary(r), "reasons": reasons})
    return {"status": "ok", "month": month, "year": year,
            "month_name": _MONTHS[month] if 0 < month < 13 else str(month),
            "count": len(anomalies), "anomalies": anomalies[:50]}


# --------------------------------------------------------------------------- #
# Draft tool — composes a reminder/approval email. It NEVER sends; the user
# copies it or opens it to send. No outward side effect.
# --------------------------------------------------------------------------- #
async def draft_reminder_email(
    db: AsyncSession, month: int, year: int,
    kind: str = "missing", employee: str | None = None,
) -> dict[str, Any]:
    """Compose (do NOT send) a reminder or approval-request email.

    kind='missing'  → a submission reminder addressed to everyone who hasn't
                      submitted for the month (or one employee if given).
    kind='approval' → an approval-request note for pending records.
    Returns {subject, body, recipients} for the user to review and send.
    """
    mname = _MONTHS[month] if 0 < month < 13 else str(month)
    recipients: list[dict[str, Any]] = []

    def _email(e: Employee) -> str | None:
        return (e.employee_email_id or (e.all_emails or "").split(";")[0] or "").strip() or None

    if str(kind).lower().startswith("appr"):
        recs = (await pending_approvals(db, month, year))["records"]
        names = ", ".join(r["employee_name"] for r in recs[:20]) or "the pending employees"
        subject = f"Approval needed — {mname} {year} timesheets"
        body = (
            f"Dear Manager,\n\nThe following {mname} {year} timesheets are awaiting your "
            f"approval:\n\n{names}\n\nPlease review and approve at your earliest "
            f"convenience.\n\nThank you,\nTimesheet Team"
        )
        return {"status": "ok", "kind": "approval", "month": month, "year": year,
                "subject": subject, "body": body,
                "recipients": [{"name": r["employee_name"]} for r in recs[:20]],
                "count": len(recs)}

    # missing-submission reminder
    if employee:
        one, rows = await _resolve_one(db, employee)
        targets = [one] if one else []
    else:
        miss = await list_missing(db, month, year)
        pks = {m["employee_pk"] for m in miss["missing"]}
        targets = list((await db.execute(select(Employee).where(
            Employee.id.in_(pks)))).scalars().all()) if pks else []
    for e in targets[:50]:
        recipients.append({"name": e.name, "employee_id": e.employee_id, "email": _email(e)})
    subject = f"Reminder — please submit your {mname} {year} timesheet"
    body = (
        f"Dear {{name}},\n\nWe have not yet received your approved timesheet for "
        f"{mname} {year}. Kindly submit it, signed/approved by your line manager, "
        f"at your earliest convenience.\n\nThank you,\nTimesheet Team"
    )
    return {"status": "ok", "kind": "missing", "month": month, "year": year,
            "subject": subject, "body": body,
            "recipients": recipients, "count": len(recipients)}
