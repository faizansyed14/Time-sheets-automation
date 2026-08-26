"""
Portal auth models — employee self-service login.

Deliberately SEPARATE from auth.py's `auth_users`/`User` (the internal
ops-team table with OTP/TOTP/CAPTCHA second-factor). Portal accounts are
admin-issued (see api/routes/admin.py's portal-user routes), so a single
bcrypt password + JWT is enough — no second factor, no self-registration.

There is no manager role/login — approval is the INTERNAL timesheet team's
job via Compare & Fix (Accept, or the "Send back" action), not a separate
manager portal step. `role` is kept as a column (still just "employee") in
case a distinct portal role is ever needed again, rather than ripped out for
a one-value field.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PortalRole:
    EMPLOYEE = "employee"
    ALL = (EMPLOYEE,)


class PortalUser(Base):
    __tablename__ = "portal_users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, index=True, default=PortalRole.EMPLOYEE)
    # Resolves to one Employee row (all_employee_data.id) — no FK, matching
    # this repo's identity-by-lookup convention elsewhere.
    employee_pk: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
