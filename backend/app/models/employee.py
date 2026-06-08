"""
all_employee_data — the authoritative employee matcher list.

Extracted employee_id / name from a timesheet is matched against this table
so records are filed under the correct person (and so we can detect who is
MISSING for a given month).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Employee(Base):
    __tablename__ = "all_employee_data"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    employee_id: Mapped[str] = mapped_column(String, index=True, unique=True)
    name: Mapped[str] = mapped_column(String, index=True)
    dco_number: Mapped[str | None] = mapped_column(String, nullable=True)
    account_manager: Mapped[str | None] = mapped_column(String, nullable=True)
    employee_email_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
