"""
Portal auth routes — employee self-service login.

Deliberately separate from /auth (the internal ops-team login with OTP/TOTP/
CAPTCHA): portal accounts are admin-issued (see admin.py's /admin/portal-users
routes), so a single username+password is enough — no second factor.

  POST /portal/auth/login
  GET  /portal/auth/me
  POST /portal/auth/logout
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_deps import get_current_portal_user
from app.core.cache import cache
from app.core.database import get_db
from app.core.security import (
    create_portal_access_token,
    decode_token,
    is_token_revoked_key,
    token_remaining_seconds,
    verify_password,
)
from app.models.employee import Employee
from app.models.portal_auth import PortalUser
from app.schemas.portal import PortalLoginIn, PortalTokenResult, PortalUserOut

router = APIRouter(prefix="/portal/auth", tags=["portal"])


async def portal_user_out(db: AsyncSession, u: PortalUser) -> PortalUserOut:
    """Shared with admin.py's portal-user management routes so both places
    resolve the same employee_name/employee_id display fields identically."""
    employee_name = employee_id = None
    if u.employee_pk:
        emp = (await db.execute(select(Employee).where(Employee.id == u.employee_pk))).scalar_one_or_none()
        if emp:
            employee_name, employee_id = emp.name, emp.employee_id
    return PortalUserOut(
        id=u.id, username=u.username, role=u.role, employee_pk=u.employee_pk,
        employee_name=employee_name, employee_id=employee_id,
        is_active=u.is_active, last_login_at=u.last_login_at,
    )


@router.post("/login", response_model=PortalTokenResult)
async def login(body: PortalLoginIn, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(PortalUser).where(PortalUser.username == body.username))).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect username or password")
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    token = create_portal_access_token(user.id, user.role)
    return PortalTokenResult(access_token=token, user=await portal_user_out(db, user))


@router.get("/me", response_model=PortalUserOut)
async def me(user: PortalUser = Depends(get_current_portal_user), db: AsyncSession = Depends(get_db)):
    return await portal_user_out(db, user)


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None)):
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if token:
        payload = decode_token(token)
        if payload and payload.get("jti"):
            await cache.set(is_token_revoked_key(payload["jti"]), "1",
                            ttl=token_remaining_seconds(payload) or 1)
    return {"status": "logged_out"}
