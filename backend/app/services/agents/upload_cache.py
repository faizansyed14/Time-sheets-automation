"""
Ephemeral in-memory store for chat-uploaded files.

A file dropped into the Agentic Chat is held in memory only long enough to be
extracted and previewed — it is NEVER written to the storage provider or the
database. Entries expire after a short TTL and the store is size-bounded, so it
cannot grow unbounded. This is deliberately not Redis/disk: the whole point is
that the document is not persisted anywhere.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

_TTL_SECONDS = 30 * 60      # 30 minutes
_MAX_ENTRIES = 50
_MAX_BYTES = 25 * 1024 * 1024  # 25 MB per file


@dataclass
class CachedUpload:
    data: bytes
    filename: str
    content_type: str
    expires_at: float


_STORE: dict[str, CachedUpload] = {}


def _evict() -> None:
    now = time.time()
    for tok in [t for t, e in _STORE.items() if e.expires_at <= now]:
        _STORE.pop(tok, None)
    # Size bound: drop the oldest entries if we are over capacity.
    if len(_STORE) > _MAX_ENTRIES:
        for tok, _ in sorted(_STORE.items(), key=lambda kv: kv[1].expires_at)[: len(_STORE) - _MAX_ENTRIES]:
            _STORE.pop(tok, None)


def put(data: bytes, filename: str, content_type: str) -> str:
    """Store bytes ephemerally; return a token to fetch them back for preview."""
    if len(data) > _MAX_BYTES:
        raise ValueError("File too large for chat preview (max 25 MB).")
    _evict()
    token = secrets.token_urlsafe(16)
    _STORE[token] = CachedUpload(
        data=data, filename=filename, content_type=content_type,
        expires_at=time.time() + _TTL_SECONDS,
    )
    return token


def get(token: str) -> CachedUpload | None:
    _evict()
    entry = _STORE.get(token)
    if entry and entry.expires_at > time.time():
        return entry
    return None
