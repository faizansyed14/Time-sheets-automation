"""Which employees belong in a given month's Dashboard / Export.

Inactivating an employee is a soft-delete (the row, records and vault files all
stay), but it must not rewrite history: someone who left in October was still
expected to submit for October and every month before it. So the rule is
month-wise, not a blanket "hide inactive":

  an employee is IN SCOPE for month M when
    - they are active, or
    - they were inactivated in M or later (``inactive_ym >= M``), or
    - the pipeline holds data for them in M (a filed record or a received
      sheet) — real data never silently drops out of a month's view.

Employees inactivated before M (or whose inactivation month is unknown) are out
of scope for M unless they have data that month.

The SQL clause (Dashboard) and the Python predicate (Export, which already
loads every employee) express the same rule — keep them in step.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import ColumnElement, or_

from app.models.employee import Employee

# UAE (Asia/Dubai) is UTC+4 year-round (no DST) — the same fixed offset
# services/reminders/service.py uses, so "the month it happened" means the same
# thing everywhere.
UAE_TZ = timezone(timedelta(hours=4))


def month_key(year: int, month: int) -> int:
    """Calendar month as a sortable integer: October 2026 -> 202610."""
    return year * 100 + month


def set_active(emp: Employee, active: bool, now: datetime | None = None) -> None:
    """Set ``emp.active`` and keep the inactivation month in step.

    Call it when the value actually changes (or on a brand-new row created
    inactive) — an ordinary edit of an already-inactive employee must not touch
    the month, so routes skip the call when ``active`` is unchanged. Marking an
    already-dated inactive employee inactive again keeps the ORIGINAL month
    (re-saving can't push the cut-off later); reactivating clears it, and
    inactivating again later records the new month.
    """
    if active:
        emp.active = True
        emp.inactivated_at = None
        emp.inactive_ym = None
        return
    already_inactive = emp.active is False
    emp.active = False
    if already_inactive and (emp.inactive_ym is not None or emp.inactivated_at is not None):
        return
    moment = (now or datetime.now(timezone.utc)).astimezone(UAE_TZ)
    emp.inactivated_at = moment
    emp.inactive_ym = month_key(moment.year, moment.month)


def in_month_clause(month: int, year: int, *has_data_pk_subqueries: Any) -> ColumnElement[bool]:
    """SQL form of the rule. Pass the month's ``submitted`` / ``received``
    employee-PK subqueries as the "has data" escape hatch."""
    ym = month_key(year, month)
    return or_(
        Employee.active.is_(True),
        Employee.inactive_ym >= ym,
        *[Employee.id.in_(s) for s in has_data_pk_subqueries],
    )


def in_month(emp: Employee, month: int, year: int, data_pks: Iterable[str] | set[str]) -> bool:
    """Python form of the rule (``data_pks`` = employee PKs with data that month)."""
    if emp.active:
        return True
    if emp.inactive_ym is not None and emp.inactive_ym >= month_key(year, month):
        return True
    return emp.id in data_pks
