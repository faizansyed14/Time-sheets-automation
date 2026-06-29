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
from app.models.timesheet_record import TimesheetRecord, ValidationStatus

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
    db: AsyncSession, employee: str, leave_type: str | None = None,
    month: int | None = None, year: int | None = None,
) -> dict[str, Any]:
    """Count an employee's leaves. If leave_type is None/'all', returns all types."""
    ts = await get_employee_timesheets(db, employee, month, year)
    if ts["status"] != "ok":
        return ts

    # No specific type requested → return full breakdown of all leave types
    if not leave_type or leave_type.strip().lower() in ("all", ""):
        summary: dict[str, Any] = {}
        for field, label in _FIELD_LABELS.items():
            total = sum(r["leaves"].get(label, 0) for r in ts["records"])
            per_record = [
                {"month": r["month"], "year": r["year"],
                 "month_name": r["month_name"], "count": r["leaves"].get(label, 0),
                 "dates": r["leave_dates"].get(label, [])}
                for r in ts["records"]
            ]
            summary[label] = {"total": total, "per_record": per_record}
        return {"status": "ok", "employee": ts["employee"], "all_leave_types": summary}

    field = _resolve_leave_field(leave_type)
    if not field:
        return {"status": "unknown_leave_type", "leave_type": leave_type,
                "valid_types": sorted(set(_FIELD_LABELS.values()))}
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
        "sick": rec.sick_leave_dates or [], "unpaid": rec.unpaid_leave_dates or [],
        "absent": rec.absent_dates or [], "public_holiday": rec.public_holiday_dates or [],
    }
    cleaned, flags = _validate(buckets, rec.month, rec.year)
    rec.annual_leave_dates = cleaned["annual"]
    rec.remote_work_dates = cleaned["remote"]
    rec.sick_leave_dates = cleaned["sick"]
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
