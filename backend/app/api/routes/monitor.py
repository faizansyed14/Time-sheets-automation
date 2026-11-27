"""Admin Monitor page: LLM calls (cost, tokens, latency, failures), Graph calls, background
jobs, API latency, live processes, queues, Redis, disk and alerts - one snapshot."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_admin

router = APIRouter(prefix="/admin/monitor", tags=["monitor"], dependencies=[Depends(require_admin)])


@router.get("")
async def monitor_snapshot(minutes: int = Query(default=60, ge=5, le=7 * 24 * 60)):
    from app.services.extract_email import auto_extract
    from app.services.inbox.sync import sync_health
    from app.services.monitoring import reader

    snap = await asyncio.to_thread(reader.snapshot, minutes)
    sync = await sync_health()
    auto = await auto_extract.get_status()
    auto_brief = {k: auto.get(k) for k in ("state", "enabled", "total", "processed", "succeeded",
                                           "failed", "skipped", "last_error", "started_at", "finished_at")}
    return {**snap, "sync": sync, "auto_extract": auto_brief,
            "alerts": reader.alerts(snap, sync=sync, auto=auto_brief)}
