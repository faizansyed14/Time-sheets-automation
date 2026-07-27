"""Shared helper for building synthetic pass-2 sheet dicts in tests — the
shape app.services.extract_email.thread_extract._normalise_pass2_sheets()
produces, which grouping.group_sheets() / auto_accept.evaluate() consume.

Leave buckets are TOP-LEVEL keys on the sheet dict (not nested under
"buckets" — that nesting only exists on the GROUP dict, one level up).
"""
from __future__ import annotations

BUCKETS = ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")


def make_sheet(
    name: str, *, kind: str = "timesheet",
    employee_name: str | None = None, employee_id: str | None = None,
    month: int | None = 6, year: int | None = 2026,
    days_covered: int = 0, period_type: str = "unknown",
    missing_days: list[int] | None = None,
    working_days: list[str] | None = None, weekend_days: list[str] | None = None,
    uncertain_days: list[dict] | None = None,
    manager_signature: bool = False, approval_evidence: str = "",
    text: str = "", notes: str = "",
    **leave_dates,
) -> dict:
    sheet = {
        "name": name, "kind": kind,
        "employee_name": employee_name, "employee_id": employee_id,
        "month": month, "year": year,
        "days_covered": days_covered, "period_type": period_type,
        "missing_days": missing_days or [],
        "working_days": working_days or [], "weekend_days": weekend_days or [],
        "uncertain_days": uncertain_days or [],
        "manager_signature": manager_signature, "approval_evidence": approval_evidence,
        "text": text, "notes": notes,
    }
    for b in BUCKETS:
        sheet[b] = sorted(leave_dates.get(b, []))
    return sheet


def full_month_sheet(name: str, month: int, year: int, **leave_dates) -> dict:
    """A complete day-by-day grid: every day of the month placed into either
    working_days or one of the leave buckets passed in — nothing unaccounted."""
    import calendar

    claimed = {d for dates in leave_dates.values() for d in dates}
    last = calendar.monthrange(year, month)[1]
    working = [f"{year:04d}-{month:02d}-{d:02d}" for d in range(1, last + 1)
              if f"{year:04d}-{month:02d}-{d:02d}" not in claimed]
    return make_sheet(
        name, month=month, year=year, days_covered=last, period_type="full_month",
        working_days=working, **leave_dates)
