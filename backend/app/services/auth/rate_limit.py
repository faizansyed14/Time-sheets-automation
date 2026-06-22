"""
Sliding-window rate limiting, backed by the cache.

`hit(scope, identifier, max, window)` records one event and returns
(allowed, retry_after_seconds). Used to throttle login and OTP-verify attempts
per username/IP so brute-force is bounded regardless of process restarts
(Redis-backed in prod, in-memory in dev).
"""
from __future__ import annotations

from app.core.cache import cache


async def hit(scope: str, identifier: str, max_events: int, window_seconds: int) -> tuple[bool, int]:
    key = f"{scope}:{identifier}"
    count = await cache.sliding_window_add(key, window_seconds)
    if count > max_events:
        return False, window_seconds
    return True, 0
