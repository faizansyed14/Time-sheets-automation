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
    employee_email_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Extended fields from the real Excel import
    project: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_no: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)   # "DXB" | "AUH"
    all_emails: Mapped[str | None] = mapped_column(String, nullable=True)  # semicolon-separated

    # Soft-delete: inactivating keeps the row (and every timesheet record /
    # vault file that references it) intact — just excluded from active
    # headline counts and matching going forward.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
