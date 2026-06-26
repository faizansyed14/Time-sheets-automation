"""
Authentication routes — the full login flow.

  POST /auth/login          username+password+captcha -> 2FA branch or session
  POST /auth/verify-otp     finish email OTP login
  POST /auth/resend-otp     resend the code
  POST /auth/verify-totp    finish authenticator (TOTP) login
  GET  /auth/captcha        fresh CAPTCHA image (login page + refresh)
  POST /auth/verify-captcha legacy captcha-mode completion (kept for API compat)
  GET  /auth/me             current user
  POST /auth/logout

Every login starts with username, password, and a CAPTCHA on the client. After
that, users with auth_mode=otp receive an email code; auth_mode=totp enter their
authenticator app code; legacy auth_mode=captcha users are signed in immediately.
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
    VerifyTotpIn,
)
from app.services.auth import captcha as captcha_svc
from app.services.auth import otp as otp_svc
from app.services.auth import rate_limit
from app.services.auth import totp as totp_svc
from app.services.tasks import send_otp_email_task

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, email=u.email, role=u.role,
                   auth_mode=u.auth_mode, is_active=u.is_active, last_login_at=u.last_login_at)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "unknown")


def _fp_from(request: Request, supplied: str | None) -> str:
    ua = request.headers.get("user-agent", "")
    header_fp = request.headers.get("x-fingerprint", "")
    return fingerprint_hash(f"{ua}|{header_fp}|{supplied or ''}")


async def _issue_session(db: AsyncSession, user: User) -> str:
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return create_access_token(user.id, user.username, user.role)


def _ensure_totp_secret(user: User) -> str:
    secret = totp_svc.decrypt_secret(user.totp_secret_enc)
    if not secret:
        secret = totp_svc.generate_secret()
        user.totp_secret_enc = totp_svc.encrypt_secret(secret)
        user.totp_enrolled = False
    return secret


def _totp_enrollment_payload(user: User, secret: str) -> dict:
    uri = totp_svc.provisioning_uri(secret, user.username)
    return {
        "totp_uri": uri,
        "totp_qr_png": totp_svc.qr_png_base64(uri),
    }


@router.post("/login", response_model=LoginResult)
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    ip = _client_ip(request)
    allowed, retry = await rate_limit.hit(
        "rl:login", f"{body.username.lower()}:{ip}",
        settings.login_rate_max, settings.login_rate_window_seconds)
    if not allowed:
        raise HTTPException(429, f"Too many login attempts. Try again in {retry}s.")

    if not await captcha_svc.verify(body.captcha_id, body.captcha_answer):
        raise HTTPException(401, "Incorrect CAPTCHA — try again.")

    user = (await db.execute(
        select(User).where(User.username == body.username))).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")

    fp = _fp_from(request, body.fingerprint)
    flow_id = secrets.token_urlsafe(16)
    login_token = create_login_token(user.id, flow_id, fp)

    # Legacy captcha-mode users: login-page CAPTCHA was their only second factor.
    if user.auth_mode == AuthMode.CAPTCHA:
        token = await _issue_session(db, user)
        return LoginResult(status="authenticated", access_token=token, user=_user_out(user))

    if user.auth_mode == AuthMode.TOTP:
        secret = _ensure_totp_secret(user)
        await db.commit()
        if not user.totp_enrolled:
            return LoginResult(
                status="totp_enrollment_required",
                login_token=login_token,
                user=_user_out(user),
                message="Scan the QR code in your authenticator app, then enter the 6-digit code.",
                **_totp_enrollment_payload(user, secret),
            )
        return LoginResult(
            status="totp_required",
            login_token=login_token,
            user=_user_out(user),
            message="Enter the 6-digit code from your authenticator app.",
        )

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


@router.post("/verify-totp", response_model=TokenResult)
async def verify_totp(body: VerifyTotpIn, request: Request, db: AsyncSession = Depends(get_db)):
    payload = _validate_login_token(body.login_token, request, body.fingerprint)
    ip = _client_ip(request)
    allowed, retry = await rate_limit.hit(
        "rl:totp", f"{payload['sub']}:{ip}",
        settings.totp_verify_rate_max, settings.totp_verify_rate_window_seconds)
    if not allowed:
        raise HTTPException(429, f"Too many attempts. Try again in {retry}s.")

    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if not user or not user.is_active or user.auth_mode != AuthMode.TOTP:
        raise HTTPException(401, "User not found or inactive")
    secret = totp_svc.decrypt_secret(user.totp_secret_enc)
    if not secret or not totp_svc.verify_code(secret, body.code):
        raise HTTPException(401, "Incorrect authenticator code — try again.")

    if not user.totp_enrolled:
        user.totp_enrolled = True
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
async def get_captcha(request: Request):
    ip = _client_ip(request)
    allowed, retry = await rate_limit.hit(
        "rl:captcha", ip, settings.captcha_rate_max, settings.captcha_rate_window_seconds)
    if not allowed:
        raise HTTPException(
            429,
            f"Too many CAPTCHA requests. Try again in {retry}s.",
            headers={"Retry-After": str(retry)},
        )
    captcha_id, png = await captcha_svc.generate()
    return Response(content=png, media_type="image/png", headers={"X-Captcha-Id": captcha_id})


@router.post("/verify-captcha", response_model=TokenResult)
async def verify_captcha(body: VerifyCaptchaIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Legacy endpoint — new logins complete via POST /login with inline CAPTCHA."""
    payload = _validate_login_token(body.login_token, request, body.fingerprint)
    allowed, retry = await rate_limit.hit(
        "rl:captcha-verify", _client_ip(request),
        settings.captcha_verify_rate_max, settings.captcha_verify_rate_window_seconds)
    if not allowed:
        raise HTTPException(429, f"Too many CAPTCHA attempts. Try again in {retry}s.")
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
