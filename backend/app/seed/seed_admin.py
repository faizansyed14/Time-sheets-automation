"""
Seed the default admin from .env on first boot (idempotent).

Username/password/email come from DEFAULT_ADMIN_* settings. The admin's role
bypasses OTP. Change the credentials in .env for production.
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
        auth_mode=AuthMode.OTP,  # ignored for admins (they bypass 2FA)
    ))
    await db.commit()
    return True
