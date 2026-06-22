"""
At-rest encryption for secret config values (API keys).

The Fernet key is derived from `jwt_secret` so there's a single secret to rotate.
Use `encrypt()` before persisting a secret and `decrypt()` after reading it.
Non-secret values are stored as plain JSON and never pass through here.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    # 32-byte key derived deterministically from the app secret.
    key = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt((plaintext or "").encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return ""
