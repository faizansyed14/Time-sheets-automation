"""System health status routes — admin-only visibility into the LLM key/
credits and Microsoft Graph credential checks (see services/system_health/
monitor.py). Mounted with require_admin at the app level (main.py), same as
reminders.router — this reads/writes nothing reminders or OTP touch.

  GET  /system-health            current status of both components
  POST /system-health/check-now  run both checks right now (e.g. right after
                                  rotating a key/secret, to confirm the fix
                                  without waiting for the next scheduled tick)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.system_health import SystemHealthCheck
from app.services.system_health import monitor

router = APIRouter(prefix="/system-health", tags=["system-health"])


def _row_out(row: SystemHealthCheck) -> dict:
    return {
        "component": row.component,
        "status": row.status,
        "detail": row.detail,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "last_ok_at": row.last_ok_at.isoformat() if row.last_ok_at else None,
        "last_alert_sent_at": row.last_alert_sent_at.isoformat() if row.last_alert_sent_at else None,
    }


@router.get("")
async def get_status(db: AsyncSession = Depends(get_db)):
    rows = await monitor.list_status(db)
    return [_row_out(r) for r in rows]


@router.post("/check-now")
async def check_now(db: AsyncSession = Depends(get_db)):
    result = await monitor.run_health_check(db)
    return result
