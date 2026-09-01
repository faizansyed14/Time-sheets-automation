"""Timesheet reminder service routes.

  GET  /reminders/config          the auto-send ON/OFF switch + fixed schedule
  PUT  /reminders/config          flip the switch
  GET  /reminders/employees       roster for a month/year, with missing/sent status
  GET  /reminders/export          XLSX of everyone missing that month's timesheet
  POST /reminders/send-now        one employee, right now (409 if already sent, unless force)
  POST /reminders/test            send the reminder design to an arbitrary address
  POST /reminders/run-batch       everyone missing this month, in the background
  GET  /reminders/runs            run history
  GET  /reminders/runs/{run_id}   one run's per-employee outcome

RBAC: require_admin at router level (main.py) — admin ONLY, every route,
including every GET. Unlike the "any full-access role" tier (calendars,
portal-users, employee matcher, ...), this router sends real email to real
employees, so it's deliberately narrower than require_full_access — not
even the standard "user" role, let alone viewer, can see or act on it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.http_headers import content_disposition
from app.models.auth import User
from app.models.employee import Employee
from app.models.reminder import EmailPreference, ReminderLog, ReminderRun, ReminderTrigger
from app.services.export.reminder_export import build_missing_reminders_xlsx
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
    auto_send_enabled: bool | None = None
    email_preference: str | None = None


def _config_out(cfg) -> dict:
    return {
        "auto_send_enabled": cfg.auto_send_enabled,
        "email_preference": cfg.email_preference,
        "send_day": service.SEND_DAY,
        "send_hour_uae": service.SEND_HOUR_UAE,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        "updated_by": cfg.updated_by,
    }


@router.get("/config")
async def get_config(db: AsyncSession = Depends(get_db)):
    return _config_out(await service.get_config(db))


@router.put("/config")
async def update_config(body: ConfigIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if body.email_preference is not None and body.email_preference not in EmailPreference.ALL:
        raise HTTPException(400, f"email_preference must be one of {EmailPreference.ALL}")
    cfg = await service.set_config(
        db, enabled=body.auto_send_enabled, email_preference=body.email_preference,
        updated_by=user.username,
    )
    return _config_out(cfg)


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
    (per the CURRENT work/personal preference — see email_source below) +
    most recent reminder outcome for this month, which that endpoint has no
    reason to carry."""
    cfg = await service.get_config(db)
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
        email, source = service.resolve_email_with_source(e, prefer=cfg.email_preference)
        rows.append({
            "employee_pk": e.id, "employee_id": e.employee_id, "name": e.name,
            "account_manager": e.account_manager, "location": e.location,
            "email": email, "email_source": source, "missing": e.id in missing,
            "last_status": l.status if l else None,
            "last_sent_at": l.sent_at.isoformat() if l and l.sent_at else None,
            "last_trigger": l.trigger if l else None,
            "last_error": l.error if l else None,
        })
    return {"total": len(filtered), "rows": rows, "email_preference": cfg.email_preference}


@router.get("/export")
async def export_missing(
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2000, le=2100),
    db: AsyncSession = Depends(get_db),
):
    """XLSX handout of everyone missing this month's timesheet — Employee ID,
    Name, Manager Name, Client, Personal Email, Work Email — so this list can
    be shared with people who don't have Reminders access, to chase down who
    hasn't sent theirs. Same missing-definition as the roster above
    (service.missing_employee_pks), not filtered by search/pagination."""
    employees = (await db.execute(
        select(Employee).where(Employee.active.is_(True)).order_by(Employee.name)
    )).scalars().all()
    active_pks = {e.id for e in employees}
    missing = await service.missing_employee_pks(db, month, year, active_employee_pks=active_pks)

    rows = [
        {
            "employee_id": e.employee_id,
            "name": e.name,
            "account_manager": e.account_manager,
            "project": e.project,
            "personal_email": e.personal_email,
            "work_email": e.work_email,
        }
        for e in employees if e.id in missing
    ]
    data = build_missing_reminders_xlsx(rows, month, year)
    fname = f"missing_timesheets_{year}-{month:02d}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition("attachment", fname)},
    )


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
async def run_batch(body: RunBatchIn, user: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
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
