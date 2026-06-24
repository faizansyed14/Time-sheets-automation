"""
Authentication routes — the full login flow.

  POST /auth/login          username+password -> (admin: authenticated) |
                            (user: otp_required + email sent) | (captcha_required)
  POST /auth/verify-otp     finish an OTP login
  POST /auth/resend-otp     resend the code (resend limit + cooldown)
  GET  /auth/captcha        fresh CAPTCHA image (also the "refresh" action)
  POST /auth/verify-captcha finish a CAPTCHA login
  GET  /auth/me             current user
  POST /auth/logout         (client drops the token; endpoint is for symmetry)

Security: per-username/IP sliding-window rate limiting, short-lived login token
binding the flow + device fingerprint, OTP expiry/attempts/resend caps,
constant-time comparisons, admin OTP bypass.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.cache import cache
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_login_token,
    decode_token,
    fingerprint_hash,
    is_token_revoked_key,
    token_remaining_seconds,
    verify_password,
)
from app.core.database import get_db
from app.models.auth import AuthMode, User
from app.schemas.auth import (
    LoginIn,
    LoginResult,
    ResendOtpIn,
    TokenResult,
    UserOut,
    VerifyCaptchaIn,
    VerifyOtpIn,
)
from app.services.auth import captcha as captcha_svc
from app.services.auth import otp as otp_svc
from app.services.auth import rate_limit
from app.services.tasks import send_otp_email_task

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, email=u.email, role=u.role,
                   auth_mode=u.auth_mode, is_active=u.is_active, last_login_at=u.last_login_at)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "unknown")


def _fp_from(request: Request, supplied: str | None) -> str:
    """Device fingerprint from User-Agent + the client-supplied id (sent either
    as the X-Fingerprint header or in the request body)."""
    ua = request.headers.get("user-agent", "")
    header_fp = request.headers.get("x-fingerprint", "")
    return fingerprint_hash(f"{ua}|{header_fp}|{supplied or ''}")


async def _issue_session(db: AsyncSession, user: User) -> str:
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return create_access_token(user.id, user.username, user.role)


@router.post("/login", response_model=LoginResult)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    ip = _client_ip(request)
    allowed, retry = await rate_limit.hit(
        "rl:login", f"{body.username.lower()}:{ip}",
        settings.login_rate_max, settings.login_rate_window_seconds)
    if not allowed:
        raise HTTPException(429, f"Too many login attempts. Try again in {retry}s.")

    user = (await db.execute(
        select(User).where(User.username == body.username))).scalar_one_or_none()
    # Constant-ish work whether or not the user exists (no username enumeration).
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")

    fp = _fp_from(request, body.fingerprint)

    # Every role — including admins — must pass the second factor (OTP/CAPTCHA).
    flow_id = secrets.token_urlsafe(16)
    login_token = create_login_token(user.id, flow_id, fp)

    if user.auth_mode == AuthMode.CAPTCHA:
        captcha_id, _img = await captcha_svc.generate()
        return LoginResult(status="captcha_required", login_token=login_token,
                           captcha_id=captcha_id, user=_user_out(user),
                           message="Solve the CAPTCHA to continue.")

    # OTP mode (default)
    res = await otp_svc.start(flow_id, user.id, user.email)
    send_otp_email_task.delay(user.email or "", res.code or "")
    return LoginResult(
        status="otp_required", login_token=login_token, user=_user_out(user),
        message=f"A code was sent to {_mask_email(user.email)}.",
        debug_otp=(res.code if not settings.is_prod else None),
    )


def _mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return "your email"
    name, dom = email.split("@", 1)
    return f"{name[:2]}***@{dom}"


def _validate_login_token(token: str, request: Request, fingerprint: str | None) -> dict:
    payload = decode_token(token)
    if not payload or payload.get("typ") != "login":
        raise HTTPException(401, "Login session expired — start again.")
    if settings.fingerprint_required:
        if payload.get("fp") != _fp_from(request, fingerprint):
            raise HTTPException(401, "Device fingerprint mismatch — start again.")
    return payload


@router.post("/verify-otp", response_model=TokenResult)
async def verify_otp(body: VerifyOtpIn, request: Request, db: AsyncSession = Depends(get_db)):
    payload = _validate_login_token(body.login_token, request, body.fingerprint)
    ip = _client_ip(request)
    allowed, retry = await rate_limit.hit(
        "rl:otp", f"{payload['sub']}:{ip}",
        settings.otp_verify_rate_max, settings.otp_verify_rate_window_seconds)
    if not allowed:
        raise HTTPException(429, f"Too many attempts. Try again in {retry}s.")

    res = await otp_svc.verify(payload["flow"], body.code)
    if not res.ok:
        raise HTTPException(401, _otp_error(res.reason))

    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    token = await _issue_session(db, user)
    return TokenResult(access_token=token, user=_user_out(user))


@router.post("/resend-otp", response_model=LoginResult)
async def resend_otp(body: ResendOtpIn, request: Request, db: AsyncSession = Depends(get_db)):
    payload = _validate_login_token(body.login_token, request, body.fingerprint)
    res = await otp_svc.resend(payload["flow"])
    if not res.ok:
        if res.reason == "cooldown":
            raise HTTPException(429, f"Please wait {res.retry_after}s before requesting another code.")
        if res.reason == "resend_limit":
            raise HTTPException(429, "Resend limit reached. Start the login again.")
        raise HTTPException(401, "Login session expired — start again.")
    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    send_otp_email_task.delay(user.email if user else "", res.code or "")
    return LoginResult(status="otp_required", login_token=body.login_token,
                       message="A new code was sent.",
                       debug_otp=(res.code if not settings.is_prod else None))


@router.get("/captcha")
async def get_captcha():
    """Fresh CAPTCHA challenge — also used by the client's 'refresh' button."""
    captcha_id, png = await captcha_svc.generate()
    return Response(content=png, media_type="image/png", headers={"X-Captcha-Id": captcha_id})


@router.post("/verify-captcha", response_model=TokenResult)
async def verify_captcha(body: VerifyCaptchaIn, request: Request, db: AsyncSession = Depends(get_db)):
    payload = _validate_login_token(body.login_token, request, body.fingerprint)
    if not await captcha_svc.verify(body.captcha_id, body.answer):
        raise HTTPException(401, "Incorrect CAPTCHA — try a new one.")
    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    token = await _issue_session(db, user)
    return TokenResult(access_token=token, user=_user_out(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return _user_out(user)


@router.post("/logout")
async def logout(authorization: str | None = Header(default=None)):
    """Real server-side logout: denylist this token's jti until it would have
    expired, so a stolen/leaked token can't be reused after sign-out."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if token:
        payload = decode_token(token)
        if payload and payload.get("jti"):
            await cache.set(is_token_revoked_key(payload["jti"]), "1",
                            ttl=token_remaining_seconds(payload) or 1)
    return {"status": "logged_out"}


def _otp_error(reason: str) -> str:
    if reason == "expired":
        return "Code expired — request a new one."
    if reason == "too_many_attempts":
        return "Too many incorrect attempts — request a new code."
    if reason.startswith("invalid_code"):
        remaining = reason.split(":", 1)[1] if ":" in reason else "0"
        return f"Incorrect code. {remaining} attempt(s) left."
    return "Invalid code."
