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
    # Restricted operator: full read + write, but ONLY on the Employee Matcher
    # and File Vault routers — every other business route (inbox, pipeline,
    # upload, timesheets, dashboard, chat) 403s for this role, even GET. See
    # api/deps.require_full_access and main.py's router wiring.
    VAULT_MATCHER = "vault_matcher"
    ALL = (ADMIN, USER, VIEWER, VAULT_MATCHER)
    WRITERS = (ADMIN, USER, VAULT_MATCHER)   # roles allowed to perform mutations


class AuthMode:
    OTP = "otp"              # email one-time code (Graph)
    TOTP = "totp"            # authenticator app (Microsoft / Google / Authy)
    CAPTCHA = "captcha"      # legacy: login-page CAPTCHA is the only 2FA step
    ALL = (OTP, TOTP, CAPTCHA)


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
    # TOTP authenticator — secret encrypted at rest; enrolled after first successful verify.
    totp_secret_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    totp_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
