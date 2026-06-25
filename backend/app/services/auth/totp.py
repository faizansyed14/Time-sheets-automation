"""
TOTP authenticator (RFC 6238) — works with Microsoft Authenticator, Google
Authenticator, Authy, etc. Secrets are stored encrypted on the user row.
"""
from __future__ import annotations

import base64
import io

import pyotp
import qrcode

from app.core.config import settings
from app.core.crypto import decrypt, encrypt


def generate_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return encrypt(secret)


def decrypt_secret(enc: str | None) -> str:
    return decrypt(enc or "")


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=username,
        issuer_name=settings.totp_issuer,
    )


def qr_png_base64(uri: str) -> str:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    normalized = "".join(c for c in code if c.isdigit())
    if len(normalized) != 6:
        return False
    return bool(pyotp.TOTP(secret).verify(normalized, valid_window=1))
