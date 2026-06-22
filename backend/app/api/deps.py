"""
Shared API dependencies: authentication + RBAC.

  get_current_user : decode the Bearer access token -> User (401 if missing/bad)
  require_user     : any authenticated active user
  require_admin    : active user with the admin role (403 otherwise)

When `auth_enabled=false` (handy for local hacking) these return a synthetic
admin so the rest of the API keeps working without logging in.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.auth import Role, User

_DEV_ADMIN = User(id="dev-admin", username="dev", email=None,
                  password_hash="", role=Role.ADMIN, auth_mode="otp", is_active=True)


def client_fingerprint(request: Request, x_fingerprint: str | None = Header(default=None)) -> str:
    """Raw fingerprint material = User-Agent + client-supplied id. Hashed later."""
    ua = request.headers.get("user-agent", "")
    return f"{ua}|{x_fingerprint or ''}"


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not settings.auth_enabled:
        return _DEV_ADMIN
    # Token comes from the Authorization header for XHR/fetch calls, OR from a
    # `token` query param for resources the BROWSER loads directly (PDF/image
    # previews in <iframe>/<img>, file downloads), which can't set headers.
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = request.query_params.get("token") or request.query_params.get("access_token")
    if not token:
        raise HTTPException(401, "Not authenticated")
    payload = decode_token(token)
    if not payload or payload.get("typ") != "access":
        raise HTTPException(401, "Invalid or expired token")
    user = (await db.execute(select(User).where(User.id == payload.get("sub")))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    return user


async def require_user(user: User = Depends(get_current_user)) -> User:
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(403, "Admin privileges required")
    return user
