"""
Cache abstraction — Redis when available, in-process dict otherwise.

Everything that needs a fast shared store (OTP state, CAPTCHA answers,
rate-limit windows, config cache) goes through this module. In production it is
backed by Redis; in dev/tests, or if Redis is unreachable, it transparently
degrades to a thread-safe in-memory store so nothing breaks and tests need no
external services.

The interface is intentionally small and async:
  get / set / delete / incr / exists  +  sliding-window helpers.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.core.config import settings

try:  # redis>=5 ships redis.asyncio
    import redis.asyncio as aioredis
except Exception:  # pragma: no cover
    aioredis = None  # type: ignore


class _MemoryCache:
    """Process-local fallback. Mimics the slice of Redis we use, with TTLs."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float | None]] = {}
        self._zsets: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    def _expired(self, key: str) -> bool:
        item = self._data.get(key)
        if not item:
            return True
        _v, exp = item
        if exp is not None and exp < time.time():
            self._data.pop(key, None)
            return True
        return False

    async def get(self, key: str) -> Any:
        async with self._lock:
            if self._expired(key):
                return None
            return self._data[key][0]

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        async with self._lock:
            self._data[key] = (value, time.time() + ttl if ttl else None)

    async def delete(self, *keys: str) -> None:
        async with self._lock:
            for k in keys:
                self._data.pop(k, None)
                self._zsets.pop(k, None)

    async def exists(self, key: str) -> bool:
        async with self._lock:
            return not self._expired(key)

    async def incr(self, key: str, ttl: int | None = None) -> int:
        async with self._lock:
            cur = 0 if self._expired(key) else int(self._data[key][0])
            cur += 1
            exp = self._data[key][1] if (key in self._data and not self._expired(key)) else (
                time.time() + ttl if ttl else None
            )
            self._data[key] = (cur, exp)
            return cur

    async def sliding_window_add(self, key: str, window_seconds: int) -> int:
        """Record one event now; return the count within the last window."""
        async with self._lock:
            now = time.time()
            bucket = [t for t in self._zsets.get(key, []) if t > now - window_seconds]
            bucket.append(now)
            self._zsets[key] = bucket
            return len(bucket)


class Cache:
    """Async facade. Picks Redis if reachable, else the in-memory fallback.
    JSON-encodes values so callers can store dicts/lists transparently."""

    def __init__(self) -> None:
        self._redis = None
        self._mem = _MemoryCache()
        self._checked = False

    async def _client(self):
        if not settings.cache_enabled or aioredis is None:
            return None
        if self._checked:
            return self._redis
        self._checked = True
        try:
            client = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            await client.ping()
            self._redis = client
        except Exception:
            self._redis = None
        return self._redis

    @staticmethod
    def _enc(value: Any) -> str:
        return json.dumps(value)

    @staticmethod
    def _dec(raw: Any) -> Any:
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    async def get(self, key: str) -> Any:
        r = await self._client()
        if r is None:
            return await self._mem.get(key)
        return self._dec(await r.get(key))

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        r = await self._client()
        if r is None:
            return await self._mem.set(key, value, ttl)
        await r.set(key, self._enc(value), ex=ttl)

    async def delete(self, *keys: str) -> None:
        r = await self._client()
        if r is None:
            return await self._mem.delete(*keys)
        if keys:
            await r.delete(*keys)

    async def exists(self, key: str) -> bool:
        r = await self._client()
        if r is None:
            return await self._mem.exists(key)
        return bool(await r.exists(key))

    async def incr(self, key: str, ttl: int | None = None) -> int:
        r = await self._client()
        if r is None:
            return await self._mem.incr(key, ttl)
        val = await r.incr(key)
        if val == 1 and ttl:
            await r.expire(key, ttl)
        return int(val)

    async def sliding_window_add(self, key: str, window_seconds: int) -> int:
        """Sliding-window counter using a Redis sorted set (timestamps), or the
        in-memory equivalent. Returns the number of events in the window."""
        r = await self._client()
        if r is None:
            return await self._mem.sliding_window_add(key, window_seconds)
        now = time.time()
        zkey = f"sw:{key}"
        pipe = r.pipeline()
        pipe.zremrangebyscore(zkey, 0, now - window_seconds)
        pipe.zadd(zkey, {f"{now}:{id(now)}": now})
        pipe.zcard(zkey)
        pipe.expire(zkey, window_seconds + 1)
        res = await pipe.execute()
        return int(res[2])

    async def ping(self) -> bool:
        return (await self._client()) is not None


cache = Cache()
