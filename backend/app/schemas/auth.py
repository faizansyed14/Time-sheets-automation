"""Auth + admin request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ---------------- login flow ----------------
class LoginIn(BaseModel):
    username: str
    password: str
    # Optional inline CAPTCHA — only meaningful for auth_mode=captcha users;
    # the normal flow answers the CAPTCHA as its own step after the password.
    captcha_id: str | None = None
    captcha_answer: str | None = None
    fingerprint: str | None = None  # opaque client device id (hashed server-side)


class VerifyOtpIn(BaseModel):
    login_token: str
    code: str
    fingerprint: str | None = None


class ResendOtpIn(BaseModel):
    login_token: str
    fingerprint: str | None = None


class VerifyCaptchaIn(BaseModel):
    login_token: str
    captcha_id: str
    answer: str
    fingerprint: str | None = None


class VerifyTotpIn(BaseModel):
    login_token: str
    code: str
    fingerprint: str | None = None


class UserOut(BaseModel):
    id: str
    username: str
    email: str | None
    role: str
    auth_mode: str
    is_active: bool
    last_login_at: datetime | None = None


class LoginResult(BaseModel):
    # status: authenticated | captcha_required | otp_required | totp_required
    #         | totp_enrollment_required
    status: str
    access_token: str | None = None
    login_token: str | None = None
    captcha_id: str | None = None
    user: UserOut | None = None
    message: str | None = None
    debug_otp: str | None = None  # populated only when not running in prod
    totp_uri: str | None = None
    totp_qr_png: str | None = None  # base64 PNG for authenticator enrollment


class TokenResult(BaseModel):
    status: str = "authenticated"
    access_token: str
    user: UserOut


# ---------------- admin: users ----------------
class AdminUserCreate(BaseModel):
    username: str
    password: str
    email: EmailStr | None = None
    role: str = "user"
    auth_mode: str = "otp"


class AdminUserUpdate(BaseModel):
    email: EmailStr | None = None
    role: str | None = None
    auth_mode: str | None = None
    is_active: bool | None = None
    password: str | None = None


class TotpSetupOut(BaseModel):
    uri: str
    qr_png: str  # base64 PNG
    manual_secret: str
    enrolled: bool


# ---------------- admin: config ----------------
class ConfigItem(BaseModel):
    key: str
    value: object | None = None
    category: str
    is_secret: bool


class ConfigUpdate(BaseModel):
    values: dict[str, object] = Field(default_factory=dict)


class ProviderTestIn(BaseModel):
    provider: str | None = None   # openai | deepseek | vllm; default = configured
    prompt: str = "Reply with the single word: OK"


class ProviderTestResult(BaseModel):
    ok: bool
    provider: str
    model: str
    latency_ms: int | None = None
    reply: str | None = None
    error: str | None = None


class AiStatusItem(BaseModel):
    """Live, resolved (not just configured) provider + model for one AI call
    site — computed the same way the actual call routes, so this can never
    drift from reality the way a static label could."""
    kind: str            # "extraction" | "validation" | "agent"
    label: str
    provider: str         # "openai" | "vllm" | "deepseek"
    model: str
    has_key: bool
    note: str | None = None
