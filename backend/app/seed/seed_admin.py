"""
Seed the default admin from .env on first boot (idempotent).

Username/password/email come from DEFAULT_ADMIN_* settings. The admin uses 2FA
like every other role; the bootstrap admin defaults to **CAPTCHA** so the first
login needs no email delivery (an admin can later switch itself to OTP from the
Users page). Change all DEFAULT_ADMIN_* credentials in .env before going live.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models.auth import AuthMode, Role, User


async def seed_admin(db: AsyncSession) -> bool:
    exists = (await db.execute(
        select(User).where(User.username == settings.default_admin_username))).scalar_one_or_none()
    if exists:
        return False
    db.add(User(
        username=settings.default_admin_username,
        email=settings.default_admin_email or None,
        password_hash=hash_password(settings.default_admin_password),
        role=Role.ADMIN,
        auth_mode=AuthMode.CAPTCHA,  # bootstrap admin uses CAPTCHA (no email needed)
    ))
    await db.commit()
    return True
