"""
Data cache — Redis-backed caching for hot read endpoints (with an in-memory
fallback via app.core.cache).

Hot aggregates (pipeline stats, dashboard headline counts) and semi-static lists
(the employee matcher) are recomputed on every request today — including the
15-second polls from the UI. This layer memoises them in Redis with short TTLs.

Invalidation uses a per-namespace VERSION counter: every cache key embeds the
current version, and `bust(ns)` just increments it — so all keys in that
namespace become unreachable instantly, with no key scanning. Writes call the
`bust_*` helpers below.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.core.cache import cache

# Namespaces
NS_PIPELINE = "pipeline"     # pipeline stats
NS_COVERAGE = "coverage"     # dashboard headline aggregates
NS_EMPLOYEES = "employees"   # employee-matcher list

# Default TTLs (seconds). Short enough that a missed bust self-heals quickly.
TTL_STATS = 15
TTL_COVERAGE = 30
TTL_EMPLOYEES = 120


async def _version(ns: str) -> int:
    v = await cache.get(f"datacache:ver:{ns}")
    try:
        return int(v) if v is not None else 1
    except (TypeError, ValueError):
        return 1


async def bust(ns: str) -> None:
    """Invalidate every key in a namespace by bumping its version."""
    try:
        await cache.set(f"datacache:ver:{ns}", await _version(ns) + 1)
    except Exception:
        pass


async def get_or_set(ns: str, key: str, ttl: int, producer: Callable[[], Awaitable[Any]]) -> Any:
    """Return the cached value for (ns, key), else run `producer`, cache, return.

    Caching is best-effort: any cache error falls through to a live `producer()`
    call so a flaky Redis can never break a request."""
    try:
        ver = await _version(ns)
        ckey = f"datacache:{ns}:v{ver}:{key}"
        hit = await cache.get(ckey)
        if hit is not None:
            return hit
    except Exception:
        ckey = None  # cache unavailable — just produce
    value = await producer()
    if ckey is not None:
        try:
            await cache.set(ckey, value, ttl=ttl)
        except Exception:
            pass
    return value


# --- convenience bust helpers wired into write paths ---
async def bust_pipeline() -> None:
    # pipeline activity also moves the dashboard's failed/needs-review counts
    await bust(NS_PIPELINE)
    await bust(NS_COVERAGE)


async def bust_coverage() -> None:
    await bust(NS_COVERAGE)


async def bust_employees() -> None:
    # the matcher list AND any coverage aggregate (employee totals) change
    await bust(NS_EMPLOYEES)
    await bust(NS_COVERAGE)
