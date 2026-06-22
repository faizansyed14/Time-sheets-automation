"""
OTP lifecycle — generation, verification, resend — with state in the cache.

State is keyed by the login-flow id (one OTP "session" per login attempt):
  {code, user_id, email, expires_at, attempts, resends, last_sent_at}

Rules enforced here:
  - expiry (otp_ttl_seconds)
  - max wrong-code attempts before the flow is burned (otp_max_attempts)
  - resend limit + cooldown (otp_resend_limit / otp_resend_cooldown_seconds)
Codes are compared in constant time and are single-use.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.cache import cache
from app.core.config import settings
from app.core.security import constant_time_equals, random_code


def _key(flow_id: str) -> str:
    return f"otp:{flow_id}"


@dataclass
class OtpResult:
    ok: bool
    reason: str = ""
    code: str | None = None          # returned only to the email sender
    retry_after: int = 0


async def _state(flow_id: str) -> dict | None:
    return await cache.get(_key(flow_id))


async def _save(flow_id: str, state: dict) -> None:
    # TTL a touch beyond expiry so verification can report "expired" cleanly.
    await cache.set(_key(flow_id), state, ttl=settings.otp_ttl_seconds + 30)


async def start(flow_id: str, user_id: str, email: str | None) -> OtpResult:
    code = random_code(settings.otp_length)
    state = {
        "code": code, "user_id": user_id, "email": email,
        "expires_at": time.time() + settings.otp_ttl_seconds,
        "attempts": 0, "resends": 0, "last_sent_at": time.time(),
    }
    await _save(flow_id, state)
    return OtpResult(ok=True, code=code)


async def resend(flow_id: str) -> OtpResult:
    state = await _state(flow_id)
    if not state:
        return OtpResult(False, "expired")
    now = time.time()
    if state["resends"] >= settings.otp_resend_limit:
        return OtpResult(False, "resend_limit")
    elapsed = now - state.get("last_sent_at", 0)
    if elapsed < settings.otp_resend_cooldown_seconds:
        return OtpResult(False, "cooldown", retry_after=int(settings.otp_resend_cooldown_seconds - elapsed))
    code = random_code(settings.otp_length)
    state.update(code=code, expires_at=now + settings.otp_ttl_seconds,
                 attempts=0, resends=state["resends"] + 1, last_sent_at=now)
    await _save(flow_id, state)
    return OtpResult(ok=True, code=code)


async def verify(flow_id: str, code: str) -> OtpResult:
    state = await _state(flow_id)
    if not state:
        return OtpResult(False, "expired")
    if time.time() > state["expires_at"]:
        await cache.delete(_key(flow_id))
        return OtpResult(False, "expired")
    if state["attempts"] >= settings.otp_max_attempts:
        await cache.delete(_key(flow_id))
        return OtpResult(False, "too_many_attempts")
    if not constant_time_equals(str(code).strip(), state["code"]):
        state["attempts"] += 1
        await _save(flow_id, state)
        remaining = settings.otp_max_attempts - state["attempts"]
        return OtpResult(False, f"invalid_code:{max(0, remaining)}")
    await cache.delete(_key(flow_id))  # single-use, consume on success
    return OtpResult(ok=True)
