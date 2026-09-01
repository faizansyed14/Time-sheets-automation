"""Core reminder orchestration — config, "who's missing", dedup, sending
(single/batch/test), and the scheduled-run gate.

Kept independent of everything else in the pipeline: reminders read
Employee/TimesheetRecord/PipelineFile but never write them, and nothing else
in the codebase reads ReminderLog/ReminderRun/ReminderConfig — purely
additive, zero risk to the existing extraction/review/vault flows.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile
from app.models.reminder import (
    EmailPreference, ReminderConfig, ReminderLog, ReminderRun, ReminderStatus, ReminderTrigger,
)
from app.models.timesheet_record import TimesheetRecord
from app.services.reminders.mailer import send_reminder_email
from app.services.reminders.template import reminder_subject, render_reminder_html

log = logging.getLogger("reminders.service")

# UAE (Asia/Dubai) is UTC+4 year-round — no DST — so a fixed offset is used
# rather than zoneinfo("Asia/Dubai"), which depends on a tzdata package being
# present in the runtime image. This is exact, not an approximation.
UAE_TZ = timezone(timedelta(hours=4))

# Fixed by product decision (not admin-editable): the 28th of every month,
# 9am UAE time. Only the ON/OFF switch (ReminderConfig.auto_send_enabled) is
# configurable — see run_scheduled_check.
SEND_DAY = 28
SEND_HOUR_UAE = 9


def uae_now() -> datetime:
    return datetime.now(UAE_TZ)


def month_label(month: int, year: int) -> str:
    """"August 2026" — the email/UI display format (contrast with
    storage_provider.month_label's "August-2026" vault-folder format)."""
    return f"{calendar.month_name[month]} {year}"


def resolve_email_with_source(e: Employee, *, prefer: str = EmailPreference.WORK) -> tuple[str | None, str]:
    """(address, source) for the reminders send path specifically — unlike
    chat_tools/exports (which just want ANY usable address), reminders lets
    the sender choose which of the two SEPARATE addresses to actually use.

    source is "work" or "personal" — whichever was actually used, which is
    `prefer` itself unless that slot is blank for this person, in which case
    the other slot is used instead — or "legacy" for a row created before
    the work/personal split existed (falls back to the older resolved
    employee_email_id) — or "none" when nothing is on file at all. The
    Reminders page shows this so a fallback is visible, not silent."""
    primary = e.personal_email if prefer == EmailPreference.PERSONAL else e.work_email
    fallback = e.work_email if prefer == EmailPreference.PERSONAL else e.personal_email
    if primary and primary.strip():
        return primary.strip(), prefer
    if fallback and fallback.strip():
        other = EmailPreference.WORK if prefer == EmailPreference.PERSONAL else EmailPreference.PERSONAL
        return fallback.strip(), other
    if e.employee_email_id and e.employee_email_id.strip():
        return e.employee_email_id.strip(), "legacy"
    return None, "none"


def resolve_email(e: Employee, *, prefer: str = EmailPreference.WORK) -> str | None:
    address, _source = resolve_email_with_source(e, prefer=prefer)
    return address


class AlreadySentError(Exception):
    """Raised by send_now when a SENT log already exists for this
    employee/month/year and force=False — carries the existing log so the
    caller (the route) can 409 with "already sent on <date>" detail."""

    def __init__(self, existing: ReminderLog):
        self.existing = existing
        super().__init__("A reminder was already sent for this employee/month.")


# ------------------------------- config -------------------------------

async def get_config(db: AsyncSession) -> ReminderConfig:
    row = (await db.execute(
        select(ReminderConfig).where(ReminderConfig.id == "singleton")
    )).scalar_one_or_none()
    if not row:
        row = ReminderConfig(id="singleton", auto_send_enabled=False)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def set_config(
    db: AsyncSession, *, enabled: bool | None = None, email_preference: str | None = None,
    updated_by: str | None,
) -> ReminderConfig:
    """Either field may be omitted to leave it unchanged — the route always
    knows both (it reads current config first), but this signature keeps
    "just flip the switch" and "just change the email preference" equally
    easy to call from anywhere else that might need it."""
    row = await get_config(db)
    if enabled is not None:
        row.auto_send_enabled = enabled
    if email_preference is not None:
        if email_preference not in EmailPreference.ALL:
            raise ValueError(f"email_preference must be one of {EmailPreference.ALL}")
        row.email_preference = email_preference
    row.updated_by = updated_by
    await db.commit()
    await db.refresh(row)
    return row


# --------------------------- who's missing? ---------------------------

async def _received_pks(db: AsyncSession, month: int, year: int) -> set[str]:
    """Any employee the pipeline has ANY staged file for this month/year,
    from ANY source (email, upload, portal, manual) — deliberately broader
    than employees.py's own `received_subq` (email-only, used for that
    page's specific KPI): a reminder must never tell someone who already
    uploaded via the Portal or the internal Upload page that "we haven't
    received" their timesheet just because it hasn't been Accepted yet."""
    pk = PipelineFile.extraction_meta["staged"]["employee_pk"].as_string()
    rows = (await db.execute(
        select(pk).where(
            PipelineFile.month == month, PipelineFile.year == year, pk.is_not(None),
        ).distinct()
    )).scalars().all()
    return set(rows)


async def _submitted_pks(db: AsyncSession, month: int, year: int) -> set[str]:
    rows = (await db.execute(
        select(TimesheetRecord.matched_employee_pk).where(
            TimesheetRecord.matched_employee_pk.is_not(None),
            TimesheetRecord.month == month, TimesheetRecord.year == year,
        ).distinct()
    )).scalars().all()
    return set(rows)


async def missing_employee_pks(
    db: AsyncSession, month: int, year: int, *, active_employee_pks: set[str]
) -> set[str]:
    """Active employees with NOTHING received/filed for month/year — the
    reminder-worthy set. `active_employee_pks` is passed in (not queried
    here) so callers that already loaded the Employee rows don't pay for a
    second query just to get the same id set."""
    covered = (await _received_pks(db, month, year)) | (await _submitted_pks(db, month, year))
    return {pk for pk in active_employee_pks if pk not in covered}


async def last_logs(db: AsyncSession, month: int, year: int, *, status: str | None = None) -> dict[str, ReminderLog]:
    """Most recent log per employee_pk for this month/year (optionally
    filtered to one status) — the "already sent on <date>" lookup."""
    conds = [ReminderLog.month == month, ReminderLog.year == year, ReminderLog.employee_pk.is_not(None)]
    if status:
        conds.append(ReminderLog.status == status)
    rows = (await db.execute(
        select(ReminderLog).where(*conds).order_by(ReminderLog.created_at.desc())
    )).scalars().all()
    out: dict[str, ReminderLog] = {}
    for r in rows:
        out.setdefault(r.employee_pk, r)  # first hit per pk = most recent (already ordered desc)
    return out


# --------------------------------- send ---------------------------------

async def _send_and_log(
    db: AsyncSession, *, employee_pk: str | None, employee_id: str | None, employee_name: str | None,
    email: str | None, month: int, year: int, trigger: str, run_id: str | None,
) -> ReminderLog:
    """The one place that actually calls the mailer and records the
    outcome — always returns a row (never raises): a bad address or a
    downed mail provider becomes a FAILED log entry, not an exception, so a
    batch loop calling this for many employees never needs its own
    try/except to keep going after one failure."""
    row = ReminderLog(
        run_id=run_id, employee_pk=employee_pk, employee_id=employee_id, employee_name=employee_name,
        recipient_email=email, month=month, year=year, trigger=trigger, status=ReminderStatus.FAILED,
    )
    try:
        if not email:
            raise ValueError("No email address on file for this employee")
        label = month_label(month, year)
        send_reminder_email(
            email, reminder_subject(label),
            render_reminder_html(employee_name=employee_name or "there", month_label=label),
        )
        row.status = ReminderStatus.SENT
        row.sent_at = datetime.now(timezone.utc)
    except Exception as exc:
        row.status = ReminderStatus.FAILED
        row.error = str(exc)[:2000]
        log.warning("Reminder send failed for %s <%s>: %s", employee_name, email, exc)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def send_now(db: AsyncSession, *, employee: Employee, month: int, year: int, force: bool = False) -> ReminderLog:
    """Per-employee "Send now" button. Raises AlreadySentError (carrying the
    prior log) when a SENT reminder already exists for this employee/month
    and force wasn't passed — the frontend shows that as a confirm dialog
    ("already sent on <date> — send again?") rather than silently
    resending or silently blocking."""
    if not force:
        existing = (await db.execute(
            select(ReminderLog).where(
                ReminderLog.employee_pk == employee.id, ReminderLog.month == month,
                ReminderLog.year == year, ReminderLog.status == ReminderStatus.SENT,
            ).order_by(ReminderLog.sent_at.desc()).limit(1)
        )).scalar_one_or_none()
        if existing:
            raise AlreadySentError(existing)
    cfg = await get_config(db)
    return await _send_and_log(
        db, employee_pk=employee.id, employee_id=employee.employee_id, employee_name=employee.name,
        email=resolve_email(employee, prefer=cfg.email_preference),
        month=month, year=year, trigger=ReminderTrigger.MANUAL_SINGLE, run_id=None,
    )


async def send_test(db: AsyncSession, *, email: str, month: int, year: int, employee_name: str) -> ReminderLog:
    """The "test email" panel — no employee record, no dedup, always sends,
    still logged (trigger=test) for audit visibility."""
    return await _send_and_log(
        db, employee_pk=None, employee_id=None, employee_name=employee_name,
        email=email, month=month, year=year, trigger=ReminderTrigger.TEST, run_id=None,
    )


async def create_run(db: AsyncSession, *, month: int, year: int, trigger: str, triggered_by: str | None = None) -> ReminderRun:
    """Just the bookkeeping row, no sending — split out from run_batch so
    the "Run for all now" HTTP route can create it synchronously (fast) and
    hand its id straight back to the caller to poll, then dispatch the
    actual sending (execute_run) to a Celery task. Without this split, the
    route would have no run_id to return until the whole batch — potentially
    slow, one Graph API call per employee — had already finished.

    started_at is set explicitly from uae_now() rather than left to the
    column's DB-clock server_default, so scheduled_run_already_done_today's
    "did today's run already happen" check always compares against the SAME
    clock it used to decide "today" in the first place (both go through
    uae_now(), which is also what a test mocks)."""
    run = ReminderRun(trigger=trigger, month=month, year=year, triggered_by=triggered_by, started_at=uae_now())
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def execute_run(db: AsyncSession, run: ReminderRun) -> ReminderRun:
    """Send to every active employee missing run.month/run.year who hasn't
    already been sent one, recording each attempt against `run`. One
    employee's failure (bad address, provider hiccup) never stops the rest —
    see _send_and_log's own always-returns-a-row contract, which is what
    makes that safe here.

    `run.total` is committed BEFORE the sending loop starts, and
    sent/failed/skipped counts are committed after every single employee
    (not just once at the end) — the manual "Run for all now" route dispatches
    this to a background task and hands the run's id straight back so the
    page can poll GET /runs/{id}; without this, that poll would show nothing
    but zeros until the entire batch (one Graph API call per employee)
    finished, real progress or not."""
    month, year, trigger = run.month, run.year, run.trigger
    cfg = await get_config(db)
    employees = (await db.execute(select(Employee).where(Employee.active.is_(True)))).scalars().all()
    active_pks = {e.id for e in employees}
    missing = await missing_employee_pks(db, month, year, active_employee_pks=active_pks)
    already_sent = await last_logs(db, month, year, status=ReminderStatus.SENT)
    candidates = [e for e in employees if e.id in missing]

    run.total = len(candidates)
    await db.commit()

    sent = failed = skipped = 0
    for e in candidates:
        prior = already_sent.get(e.id)
        if prior:
            db.add(ReminderLog(
                run_id=run.id, employee_pk=e.id, employee_id=e.employee_id, employee_name=e.name,
                recipient_email=resolve_email(e, prefer=cfg.email_preference), month=month, year=year, trigger=trigger,
                status=ReminderStatus.SKIPPED,
                error=f"Already sent on {prior.sent_at.isoformat() if prior.sent_at else '?'}",
            ))
            skipped += 1
        else:
            row = await _send_and_log(
                db, employee_pk=e.id, employee_id=e.employee_id, employee_name=e.name,
                email=resolve_email(e, prefer=cfg.email_preference),
                month=month, year=year, trigger=trigger, run_id=run.id,
            )
            sent += row.status == ReminderStatus.SENT
            failed += row.status == ReminderStatus.FAILED
        run.sent_count, run.failed_count, run.skipped_count = sent, failed, skipped
        await db.commit()

    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    return run


async def run_batch(db: AsyncSession, *, month: int, year: int, trigger: str, triggered_by: str | None = None) -> ReminderRun:
    """create_run + execute_run in one call — used by the scheduled check
    below, which is already running inside its own Celery task and has no
    need for the create-then-dispatch split run-batch's HTTP route uses."""
    run = await create_run(db, month=month, year=year, trigger=trigger, triggered_by=triggered_by)
    return await execute_run(db, run)


async def scheduled_run_already_done_today(db: AsyncSession) -> bool:
    """Idempotency guard: an hourly beat tick that lands on hour 9 could in
    principle fire more than once (worker restart, clock skew), and this
    must never send a second round of reminders the same day."""
    today = uae_now().date()
    rows = (await db.execute(
        select(ReminderRun).where(ReminderRun.trigger == ReminderTrigger.SCHEDULED)
        .order_by(ReminderRun.started_at.desc()).limit(5)
    )).scalars().all()
    return any(r.started_at and r.started_at.astimezone(UAE_TZ).date() == today for r in rows)


async def run_scheduled_check(db: AsyncSession) -> dict:
    """Called every hour by Celery beat (see core/celery_app.py). Actually
    sending only happens when ALL of: the ON/OFF switch is on, it's the
    28th at 9am UAE, and today's scheduled run hasn't already happened —
    the switch is a runtime DB value, not something that can gate the
    static beat_schedule at process-startup time, so the gate lives here
    instead, checked on every tick."""
    if not settings.reminder_scheduled_check_enabled:
        return {"ran": False, "reason": "reminder scheduled check disabled by env"}
    cfg = await get_config(db)
    if not cfg.auto_send_enabled:
        return {"ran": False, "reason": "auto-send is off"}
    now = uae_now()
    if now.day != SEND_DAY or now.hour != SEND_HOUR_UAE:
        return {"ran": False, "reason": "not the scheduled time"}
    if await scheduled_run_already_done_today(db):
        return {"ran": False, "reason": "already ran today"}
    run = await run_batch(db, month=now.month, year=now.year, trigger=ReminderTrigger.SCHEDULED)
    return {"ran": True, "run_id": run.id}
