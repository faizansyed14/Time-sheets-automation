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
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint, func
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
    # every existing caller that just needs ONE usable address (chat_tools,
    # exports, inbox matching). Reminders is the one caller that does NOT use
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
