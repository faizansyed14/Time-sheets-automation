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

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_deps import get_current_portal_user
from app.core.cache import cache
from app.core.config import settings
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
from app.services.auth import rate_limit

router = APIRouter(prefix="/portal/auth", tags=["portal"])


def _client_ip(request: Request) -> str:
    """Real client IP for rate-limiting keys — same precedence as auth.py's
    own _client_ip (kept as a separate copy rather than a cross-module import
    so this route file has no dependency on the internal /auth router).

    Our nginx sets `X-Real-IP $remote_addr` (the actual socket IP) and
    OVERWRITES any client-supplied value, so it cannot be spoofed — prefer it.
    Never trust the FIRST X-Forwarded-For entry (the client controls it); if
    XREAL is absent fall back to the LAST XFF hop (the one our proxy appended
    via proxy_add_x_forwarded_for), then the direct socket."""
    xreal = (request.headers.get("x-real-ip") or "").strip()
    if xreal:
        return xreal
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        hops = [p.strip() for p in fwd.split(",") if p.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


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
async def login(body: PortalLoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    # Portal accounts are single-factor (password only, no OTP/TOTP — see
    # this file's own module docstring), so this is the ONLY brute-force
    # defense they get. A distinct "rl:portal-login" scope (not "rl:login")
    # keeps this bucket separate from the internal /auth/login one, so the
    # two user populations sharing an office IP can never exhaust each
    # other's attempt budget. Same threshold as /auth/login (settings.
    # login_rate_max/window) — same security stance, no new config to add.
    ip = _client_ip(request)
    allowed, retry = await rate_limit.hit(
        "rl:portal-login", f"{body.username.lower()}:{ip}",
        settings.login_rate_max, settings.login_rate_window_seconds)
    if not allowed:
        raise HTTPException(429, f"Too many login attempts. Try again in {retry}s.")

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
