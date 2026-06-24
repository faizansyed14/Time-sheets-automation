"""
Shared API dependencies: authentication + RBAC.

  get_current_user : decode the Bearer access token -> User (401 if missing/bad).
                     Rejects tokens that were revoked at logout (jti denylist).
  require_user     : any authenticated active user (admin / user / viewer)
  require_write    : authenticated user whose role may MUTATE data. Viewers get
                     403 on any non-safe (write) HTTP method.
  require_admin    : active user with the admin role (403 otherwise)

When `auth_enabled=false` (local hacking only) these return a synthetic admin so
the rest of the API keeps working without logging in.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token, is_token_revoked_key
from app.models.auth import Role, User

# HTTP methods that never mutate state — viewers are allowed these.
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

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
    # Server-side revocation: a token whose jti was denylisted at logout is dead
    # even though it hasn't expired yet.
    jti = payload.get("jti")
    if jti and await cache.exists(is_token_revoked_key(jti)):
        raise HTTPException(401, "Session ended — please sign in again.")
    user = (await db.execute(select(User).where(User.id == payload.get("sub")))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    return user


async def require_user(user: User = Depends(get_current_user)) -> User:
    return user


async def require_write(request: Request, user: User = Depends(get_current_user)) -> User:
    """Allow reads for everyone; block mutations for the read-only `viewer` role.

    Applied at the router level so every write endpoint is covered without
    per-route changes — defence in depth even if a route forgets to check."""
    if request.method.upper() not in _SAFE_METHODS and user.role not in Role.WRITERS:
        raise HTTPException(403, "Your role has read-only access.")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(403, "Admin privileges required")
    return user
