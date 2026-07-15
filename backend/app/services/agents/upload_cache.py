"""
Ephemeral store for chat-uploaded files.

Bytes are held only long enough to extract and preview — never written to the
storage provider or database until the user opts in via /attachments/{token}/store.
Uses Redis when available so multiple uvicorn workers share the same tokens;
falls back to process-local memory when Redis is unreachable (dev/tests).
"""
from __future__ import annotations

import base64
import secrets
import time
from dataclasses import dataclass

from app.core.cache import cache

_TTL_SECONDS = 30 * 60      # 30 minutes
_MAX_ENTRIES = 50
_MAX_BYTES = 25 * 1024 * 1024  # 25 MB per file
_KEY_PREFIX = "chat_upload:"


@dataclass
class CachedUpload:
    data: bytes
    filename: str
    content_type: str
    expires_at: float


# Process-local fallback when Redis is unavailable.
_LOCAL: dict[str, CachedUpload] = {}


def _local_evict() -> None:
    now = time.time()
    for tok in [t for t, e in _LOCAL.items() if e.expires_at <= now]:
        _LOCAL.pop(tok, None)
    if len(_LOCAL) > _MAX_ENTRIES:
        for tok, _ in sorted(_LOCAL.items(), key=lambda kv: kv[1].expires_at)[: len(_LOCAL) - _MAX_ENTRIES]:
            _LOCAL.pop(tok, None)


def _from_payload(raw: dict) -> CachedUpload | None:
    try:
        exp = float(raw["expires_at"])
        if exp <= time.time():
            return None
        return CachedUpload(
            data=base64.b64decode(raw["data_b64"]),
            filename=raw["filename"],
            content_type=raw["content_type"],
            expires_at=exp,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _to_payload(entry: CachedUpload) -> dict:
    return {
        "data_b64": base64.b64encode(entry.data).decode("ascii"),
        "filename": entry.filename,
        "content_type": entry.content_type,
        "expires_at": entry.expires_at,
    }


async def put(data: bytes, filename: str, content_type: str) -> str:
    """Store bytes ephemerally; return a token to fetch them back for preview."""
    if len(data) > _MAX_BYTES:
        raise ValueError("File too large for chat preview (max 25 MB).")
    token = secrets.token_urlsafe(16)
    entry = CachedUpload(
        data=data, filename=filename, content_type=content_type,
        expires_at=time.time() + _TTL_SECONDS,
    )
    key = f"{_KEY_PREFIX}{token}"
    if await cache.ping():
        await cache.set(key, _to_payload(entry), ttl=_TTL_SECONDS)
    else:
        _local_evict()
        _LOCAL[token] = entry
    return token


async def get(token: str) -> CachedUpload | None:
    key = f"{_KEY_PREFIX}{token}"
    if await cache.ping():
        raw = await cache.get(key)
        if not raw:
            return None
        entry = _from_payload(raw)
        if entry is None:
            await cache.delete(key)
        return entry
    _local_evict()
    entry = _LOCAL.get(token)
    if entry and entry.expires_at > time.time():
        return entry
    return None


async def pop(token: str) -> CachedUpload | None:
    """Fetch and remove — used once when the user opts to persist via pipeline."""
    entry = await get(token)
    if not entry:
        return None
    key = f"{_KEY_PREFIX}{token}"
    if await cache.ping():
        await cache.delete(key)
    else:
        _LOCAL.pop(token, None)
    return entry
