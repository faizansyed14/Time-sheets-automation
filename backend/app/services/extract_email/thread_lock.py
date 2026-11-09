"""Locks that keep concurrent work from double-processing.

Per-conversation extraction (`thread_lock`)
-------------------------------------------
The SAME conversation can be extracted from several places at once — Auto
Extract's Celery task, a second worker picking up a duplicate dispatch, a user
clicking Extract (or "Extract selected") while a background run is still
going. Unserialised, two runs read the same attachments twice (paid LLM calls),
then race to stage the same review items.

One lock per thread key, in Redis (shared by the API process and every worker):
  * SET NX EX — atomic acquire, auto-expires so a crashed worker can't wedge a
    thread forever;
  * a heartbeat keeps it alive while a long run is genuinely still working;
  * release only deletes the lock if we still own it (token compare).

A caller that finds the lock held WAITS (it does not fail and does not start a
second run). When it finally gets the lock, `waited=True` tells it to re-check
whether the run it waited for already covered everything, and skip the
duplicate work if so (see email.extract_full_email).

Per-record filing (`keyed_lock`)
--------------------------------
The same primitive, keyed by employee + month, serialises Accept so two
simultaneous Accepts cannot both decide "there is no record yet" and create two
(see pipeline.ingestion.ingest_manual_entry).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.core.cache import cache
from app.services.extract_email.progress import emit

log = logging.getLogger("extract_email.lock")

LOCK_TTL = 300            # seconds a lock survives without a heartbeat
HEARTBEAT_EVERY = 90      # seconds between heartbeats while the run is alive
MAX_WAIT = 3 * 3600       # a run queued behind another waits at most this long
POLL_EVERY = 2.0


class ThreadBusy(RuntimeError):
    """The resource is being worked on by someone else and we gave up waiting."""


def lock_key(thread_key: str) -> str:
    return f"extract:lock:{thread_key}"


async def _heartbeat(key: str, token: str) -> None:
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_EVERY)
            if not await cache.extend_lock(key, token, LOCK_TTL):
                log.warning("lock %s was lost while the work was still active", key)
                return
    except asyncio.CancelledError:
        pass
    except Exception:       # a failed heartbeat must never kill the work
        log.exception("lock heartbeat failed for %s", key)


@asynccontextmanager
async def keyed_lock(
    key: str, *, max_wait: float = MAX_WAIT,
    waiting_message: str = "Another run is using this — waiting for it…",
    busy_message: str = "Still being processed by another run — try again shortly.",
) -> AsyncIterator[bool]:
    """Hold the named lock (any key). Yields `waited` — True if it was busy on
    the first try."""
    token = uuid.uuid4().hex
    waited = False
    started = time.monotonic()
    while not await cache.acquire_lock(key, LOCK_TTL, token):
        if not waited:
            waited = True
            emit("unpack", "spin", waiting_message)
        if time.monotonic() - started > max_wait:
            raise ThreadBusy(busy_message)
        await asyncio.sleep(POLL_EVERY)
    beat = asyncio.create_task(_heartbeat(key, token))
    try:
        yield waited
    finally:
        beat.cancel()
        try:
            await beat
        except BaseException:
            pass
        try:
            await cache.release_lock(key, token)
        except Exception:
            log.exception("could not release lock %s (it will expire on its own)", key)


@asynccontextmanager
async def thread_lock(thread_key: str, *, max_wait: float = MAX_WAIT) -> AsyncIterator[bool]:
    """Hold the conversation's extraction lock. Yields `waited` (True if the
    lock was busy on first try)."""
    async with keyed_lock(
        lock_key(thread_key), max_wait=max_wait,
        waiting_message=("Another extraction of this conversation is already running — waiting "
                         "for it instead of starting a duplicate…"),
        busy_message=("This conversation is still being extracted by another run — "
                      "try again shortly."),
    ) as waited:
        yield waited
