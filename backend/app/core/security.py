"""
Security primitives: password hashing (bcrypt) and JWT tokens.

Two token kinds:
  - "login"  : short-lived, issued after a correct password while the second
               factor (OTP/CAPTCHA) is still pending. Carries the login flow id.
  - "access" : full session token issued after the second factor is satisfied.
               Carries the role and a unique jti (so it can be revoked at logout).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

import bcrypt
import jwt

from app.core.config import settings


# --------------------------- passwords ---------------------------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# --------------------------- JWT ---------------------------
def _encode(payload: dict, ttl_minutes: int) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    body = {**payload, "iat": now, "exp": now + dt.timedelta(minutes=ttl_minutes)}
    return jwt.encode(body, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except Exception:
        return None


def create_login_token(user_id: str, flow_id: str, fingerprint: str) -> str:
    """Issued after a correct password; the 2FA step must present it back."""
    return _encode(
        {"sub": user_id, "typ": "login", "flow": flow_id, "fp": fingerprint},
        settings.login_token_ttl_minutes,
    )


def create_access_token(user_id: str, username: str, role: str) -> str:
    return _encode(
        {"sub": user_id, "typ": "access", "username": username, "role": role,
         "jti": secrets.token_urlsafe(16)},
        settings.access_token_ttl_minutes,
    )


def is_token_revoked_key(jti: str) -> str:
    """Cache key under which a revoked (logged-out) token's jti is stored."""
    return f"revoked_jti:{jti}"


def token_remaining_seconds(payload: dict) -> int:
    """Seconds until a decoded token expires (>= 0), for sizing a denylist TTL."""
    exp = payload.get("exp")
    if not exp:
        return 0
    try:
        remaining = int(exp - dt.datetime.now(dt.timezone.utc).timestamp())
    except Exception:
        return 0
    return max(0, remaining)


# --------------------------- misc ---------------------------
def random_code(length: int) -> str:
    """A numeric OTP, cryptographically random, zero-padded."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


def fingerprint_hash(raw: str) -> str:
    """Stable hash of a client fingerprint (UA + accept headers + client id)."""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()[:32]


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())
