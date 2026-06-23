"""
Auth models — application users with RBAC + per-user second-factor mode.

OTP *lifecycle* state (codes, attempts, resends, expiry) lives in the cache
(Redis / in-memory) keyed by the login-flow id, not here — so this table stays
small and contains no secrets beyond the bcrypt password hash.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Role:
    ADMIN = "admin"     # full access (users, config, all reads + writes)
    USER = "user"       # standard operator: all business reads + writes
    VIEWER = "viewer"   # read-only: may view everything, may not mutate anything
    ALL = (ADMIN, USER, VIEWER)
    WRITERS = (ADMIN, USER)   # roles allowed to perform mutations


class AuthMode:
    OTP = "otp"          # email one-time code (Graph)
    CAPTCHA = "captcha"  # word CAPTCHA challenge
    ALL = (OTP, CAPTCHA)


class User(Base):
    __tablename__ = "auth_users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)   # OTP delivery address
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default=Role.USER, index=True)
    # Second factor (OTP/CAPTCHA) required after a correct password — for EVERY
    # role, including admins.
    auth_mode: Mapped[str] = mapped_column(String, default=AuthMode.OTP)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
