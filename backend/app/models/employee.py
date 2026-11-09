"""
all_employee_data — the authoritative employee matcher list.

Extracted employee_id / name from a timesheet is matched against this table
so records are filed under the correct person (and so we can detect who is
MISSING for a given month).

NOTE: employee_id is NOT unique on its own — the AUH and DXB teams have
overlapping ID ranges, so the same employee_id can exist twice with different
names. Identity is (employee_id, name); matching disambiguates by name.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Employee(Base):
    __tablename__ = "all_employee_data"
    __table_args__ = (
        UniqueConstraint("employee_id", "name", name="uq_employee_id_name"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    employee_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    # ACO / DCO reference numbers. Both are written into the employee's File
    # Vault folder name at filing time (see storage_provider.employee_folder_label)
    # so a folder identifies the person by contract number, not just by name.
    aco_number: Mapped[str | None] = mapped_column(String, nullable=True)
    dco_number: Mapped[str | None] = mapped_column(String, nullable=True)
    account_manager: Mapped[str | None] = mapped_column(String, nullable=True)
    # The resolved "primary" address (work if set, else personal) — kept for
    # every existing caller that just needs ONE usable address (exports,
    # inbox matching). Reminders is the one caller that does NOT use
    # this — it lets the sender explicitly choose work vs personal instead
    # (see reminders/service.py's resolve_email).
    employee_email_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Extended fields from the real Excel import
    project: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_no: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)   # "DXB" | "AUH"
    # Kept SEPARATE from each other — a source sheet that lists both a
    # company address and a personal one must never have them silently
    # merged into one blob (that WAS what the old all_emails column did;
    # removed once work/personal replaced every reason to keep it — see
    # import_service.py's AUH/DXB parsers for how each is populated).
    work_email: Mapped[str | None] = mapped_column(String, nullable=True)
    personal_email: Mapped[str | None] = mapped_column(String, nullable=True)

    # Soft-delete: inactivating keeps the row (and every timesheet record /
    # vault file that references it) intact — just excluded from active
    # headline counts and matching going forward.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # WHEN they were marked inactive, so Dashboard/Export can stay month-wise:
    # an inactive employee still appears for the month they were inactivated
    # (and every month before it) and drops off from the NEXT month. NULL while
    # active, and for rows that were already inactive before these columns
    # existed (their month is unknown — they only show in months where they
    # have data). `inactive_ym` is that moment's calendar month in UAE time as
    # year*100+month (e.g. 202610), so the month test is one integer compare in
    # SQL. Always set through services/employee/activity.set_active().
    inactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inactive_ym: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Joining date. The person is counted from the month it falls in; earlier months keep
    # their old headcount. Stamped (today, UAE) when an employee is ADDED to an existing
    # roster, editable in the form. NULL = "since always" (the first bulk load and every
    # row that existed before this column) - never hidden from any month.
    joining_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
