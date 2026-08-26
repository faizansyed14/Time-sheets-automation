"""
Portal API dependencies: authentication + role gating for the employee
self-service portal.

Deliberately separate from api/deps.py's internal `get_current_user` — a
portal token carries typ="portal_access" (see core/security.py's
create_portal_access_token) and is rejected by the internal dependency; an
internal "access" token is rejected here. Same signing secret, two isolated
namespaces, no new setting needed.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.core.database import get_db
from app.core.security import decode_token, is_token_revoked_key
from app.models.portal_auth import PortalRole, PortalUser


async def get_current_portal_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> PortalUser:
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = request.query_params.get("token") or request.query_params.get("access_token")
    if not token:
        raise HTTPException(401, "Not authenticated")
    payload = decode_token(token)
    if not payload or payload.get("typ") != "portal_access":
        raise HTTPException(401, "Invalid or expired token")
    jti = payload.get("jti")
    if jti and await cache.exists(is_token_revoked_key(jti)):
        raise HTTPException(401, "Session ended — please sign in again.")
    user = (await db.execute(select(PortalUser).where(PortalUser.id == payload.get("sub")))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "Account not found or inactive")
    return user


async def require_portal_employee(user: PortalUser = Depends(get_current_portal_user)) -> PortalUser:
    if user.role != PortalRole.EMPLOYEE:
        raise HTTPException(403, "Employee account required")
    return user
