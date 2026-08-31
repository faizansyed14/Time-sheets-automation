"""Timesheet reminder service routes.

  GET  /reminders/config          the auto-send ON/OFF switch + fixed schedule
  PUT  /reminders/config          flip the switch
  GET  /reminders/employees       roster for a month/year, with missing/sent status
  POST /reminders/send-now        one employee, right now (409 if already sent, unless force)
  POST /reminders/test            send the reminder design to an arbitrary address
  POST /reminders/run-batch       everyone missing this month, in the background
  GET  /reminders/runs            run history
  GET  /reminders/runs/{run_id}   one run's per-employee outcome

RBAC: require_full_access at router level (main.py) — any full-access role
(admin/user) may view AND act; viewers read-only (GETs pass, writes 403);
the restricted vault_matcher role has no access at all, same as every other
business router.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_full_access
from app.core.database import get_db
from app.models.auth import User
from app.models.employee import Employee
from app.models.reminder import ReminderLog, ReminderRun, ReminderTrigger
from app.services.reminders import service

router = APIRouter(prefix="/reminders", tags=["reminders"])


def _log_out(row: ReminderLog) -> dict:
    return {
        "id": row.id, "run_id": row.run_id, "employee_pk": row.employee_pk,
        "employee_id": row.employee_id, "employee_name": row.employee_name,
        "recipient_email": row.recipient_email, "month": row.month, "year": row.year,
        "trigger": row.trigger, "status": row.status, "error": row.error,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _run_out(run: ReminderRun) -> dict:
    return {
        "id": run.id, "trigger": run.trigger, "month": run.month, "year": run.year,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "total": run.total, "sent_count": run.sent_count, "failed_count": run.failed_count,
        "skipped_count": run.skipped_count, "triggered_by": run.triggered_by,
    }


# --------------------------------- config ---------------------------------

class ConfigIn(BaseModel):
    auto_send_enabled: bool


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db)):
    cfg = await service.get_config(db)
    return {
        "auto_send_enabled": cfg.auto_send_enabled,
        "send_day": service.SEND_DAY,
        "send_hour_uae": service.SEND_HOUR_UAE,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        "updated_by": cfg.updated_by,
    }


@router.put("/config")
async def update_config(body: ConfigIn, user: User = Depends(require_full_access), db: AsyncSession = Depends(get_db)):
    cfg = await service.set_config(db, enabled=body.auto_send_enabled, updated_by=user.username)
    return {
        "auto_send_enabled": cfg.auto_send_enabled,
        "send_day": service.SEND_DAY,
        "send_hour_uae": service.SEND_HOUR_UAE,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        "updated_by": cfg.updated_by,
    }


# ------------------------------- employees --------------------------------

@router.get("/employees")
async def list_employees(
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2000, le=2100),
    q: str | None = Query(default=None),
    only_missing: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """The Reminders page's own roster view — deliberately independent of
    employees.py's /coverage (different "received" definition, see
    service.missing_employee_pks) and returns each employee's resolved email
    + most recent reminder outcome for this month, which that endpoint has no
    reason to carry."""
    employees = (await db.execute(
        select(Employee).where(Employee.active.is_(True)).order_by(Employee.name)
    )).scalars().all()
    active_pks = {e.id for e in employees}
    missing = await service.missing_employee_pks(db, month, year, active_employee_pks=active_pks)
    last = await service.last_logs(db, month, year)

    def matches(e: Employee) -> bool:
        if not q:
            return True
        ql = q.lower()
        return ql in (e.name or "").lower() or ql in (e.employee_id or "").lower() \
            or ql in (e.account_manager or "").lower()

    filtered = [e for e in employees if matches(e) and (not only_missing or e.id in missing)]
    page = filtered[offset: offset + limit]

    rows = []
    for e in page:
        l = last.get(e.id)
        rows.append({
            "employee_pk": e.id, "employee_id": e.employee_id, "name": e.name,
            "account_manager": e.account_manager, "location": e.location,
            "email": service.resolve_email(e), "missing": e.id in missing,
            "last_status": l.status if l else None,
            "last_sent_at": l.sent_at.isoformat() if l and l.sent_at else None,
            "last_trigger": l.trigger if l else None,
            "last_error": l.error if l else None,
        })
    return {"total": len(filtered), "rows": rows}


# ---------------------------------- send -----------------------------------

class SendNowIn(BaseModel):
    employee_pk: str
    month: int
    year: int
    force: bool = False


@router.post("/send-now")
async def send_now(body: SendNowIn, db: AsyncSession = Depends(get_db)):
    employee = (await db.execute(select(Employee).where(Employee.id == body.employee_pk))).scalar_one_or_none()
    if not employee:
        raise HTTPException(404, "Employee not found")
    try:
        row = await service.send_now(db, employee=employee, month=body.month, year=body.year, force=body.force)
    except service.AlreadySentError as exc:
        raise HTTPException(409, {
            "message": "A reminder was already sent to this employee for this month.",
            "sent_at": exc.existing.sent_at.isoformat() if exc.existing.sent_at else None,
        })
    return _log_out(row)


class TestIn(BaseModel):
    email: str
    month: int
    year: int
    employee_name: str = "Test Employee"


@router.post("/test")
async def send_test(body: TestIn, db: AsyncSession = Depends(get_db)):
    if "@" not in body.email:
        raise HTTPException(400, "Enter a valid email address")
    row = await service.send_test(db, email=body.email, month=body.month, year=body.year, employee_name=body.employee_name)
    return _log_out(row)


# ---------------------------------- runs -----------------------------------

class RunBatchIn(BaseModel):
    month: int | None = None
    year: int | None = None


@router.post("/run-batch")
async def run_batch(body: RunBatchIn, user: User = Depends(require_full_access), db: AsyncSession = Depends(get_db)):
    """Admin's "Run for all now". The run row is created synchronously (fast
    — no sending yet) so its id can be returned right away; the actual
    sending (one Graph call per missing employee) runs in a Celery task —
    with celery_task_always_eager (dev/tests) that still executes inline
    before this returns, but in production the request never blocks on it.
    The page polls GET /runs/{id} until finished_at is set."""
    from app.services.tasks import run_reminder_execute_task

    now = service.uae_now()
    month = body.month or now.month
    year = body.year or now.year
    run = await service.create_run(
        db, month=month, year=year, trigger=ReminderTrigger.MANUAL_BATCH, triggered_by=user.username)
    run_reminder_execute_task.delay(run.id)
    return {"run_id": run.id}


@router.get("/runs")
async def list_runs(limit: int = Query(default=20, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    runs = (await db.execute(
        select(ReminderRun).order_by(ReminderRun.started_at.desc()).limit(limit)
    )).scalars().all()
    return [_run_out(r) for r in runs]


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = (await db.execute(select(ReminderRun).where(ReminderRun.id == run_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    logs = (await db.execute(
        select(ReminderLog).where(ReminderLog.run_id == run_id).order_by(ReminderLog.employee_name)
    )).scalars().all()
    return {**_run_out(run), "logs": [_log_out(l) for l in logs]}
