"""
Admin routes.

Users, config and debug traces below are admin-only. Month calendars are the
one exception — they're read by every extraction run and relevant to normal
timesheet review, not just admin configuration, so they live on their own
router with the same read/write split the business routes use (require_write):
any authenticated role can view them, but only admin/user (not viewer) can
edit — see `calendars_router` below.

Users (RBAC: admin only):
  GET    /admin/users                list
  POST   /admin/users                create (assign email for OTP, set role + auth_mode)
  PATCH  /admin/users/{id}           update (email, role, auth_mode, active, password)
  POST   /admin/users/{id}/auth-mode switch OTP <-> CAPTCHA
  DELETE /admin/users/{id}

Config (RBAC: admin only; read-only status from .env):
  GET    /admin/config/status        resolved models + key status

Month calendars (RBAC: any role reads, admin/user writes):
  GET    /admin/calendars            list
  PUT    /admin/calendars            upsert (by month+year)
  DELETE /admin/calendars/{id}

Extraction debug runs (RBAC: admin only; temporary, purgeable — see debug_capture.py):
  GET    /admin/debug/runs           list (summary only)
  GET    /admin/debug/runs/{id}      full detail (prompts, responses, dropped items, sheets)
  GET    /admin/debug/image          serve one saved dropped-item image
  DELETE /admin/debug/runs           bulk-delete everything
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_write
from app.core.database import get_db
from app.core.security import hash_password
from app.models.auth import AuthMode, Role, User
from app.models.extraction_debug_run import ExtractionDebugRun
from app.models.month_calendar import MonthCalendar
from app.schemas import DebugRunOut, DebugRunSummary, MonthCalendarIn, MonthCalendarOut
from app.schemas.auth import (
    AdminUserCreate,
    AdminUserUpdate,
    AiStatusItem,
    TotpSetupOut,
    UserOut,
)
from app.services.auth import totp as totp_svc

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
calendars_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_write)])


MIN_PASSWORD_LEN = 8


def _check_password(pw: str) -> None:
    if not pw or len(pw) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters.")


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, email=u.email, role=u.role,
                   auth_mode=u.auth_mode, is_active=u.is_active, last_login_at=u.last_login_at)


def _provision_totp(u: User, *, reset: bool = False) -> TotpSetupOut:
    if reset or not u.totp_secret_enc:
        secret = totp_svc.generate_secret()
        u.totp_secret_enc = totp_svc.encrypt_secret(secret)
        u.totp_enrolled = False
    else:
        secret = totp_svc.decrypt_secret(u.totp_secret_enc)
    uri = totp_svc.provisioning_uri(secret, u.username)
    return TotpSetupOut(
        uri=uri,
        qr_png=totp_svc.qr_png_base64(uri),
        manual_secret=secret,
        enrolled=u.totp_enrolled,
    )


def _clear_totp(u: User) -> None:
    u.totp_secret_enc = None
    u.totp_enrolled = False


# ----------------------------- users -----------------------------
@router.get("/users", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(User).order_by(User.username))).scalars().all()
    return [_user_out(u) for u in rows]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: AdminUserCreate, db: AsyncSession = Depends(get_db)):
    if body.role not in Role.ALL:
        raise HTTPException(400, f"role must be one of {Role.ALL}")
    if body.auth_mode not in AuthMode.ALL:
        raise HTTPException(400, f"auth_mode must be one of {AuthMode.ALL}")
    dup = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "Username already exists")
    _check_password(body.password)
    # Every role (including admin) uses 2FA, so OTP mode always needs an email.
    if body.auth_mode == AuthMode.OTP and not body.email:
        raise HTTPException(400, "An email is required for OTP delivery")
    u = User(username=body.username, email=str(body.email) if body.email else None,
             password_hash=hash_password(body.password), role=body.role, auth_mode=body.auth_mode)
    if body.auth_mode == AuthMode.TOTP:
        _provision_totp(u)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return _user_out(u)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: AdminUserUpdate, db: AsyncSession = Depends(get_db)):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")
    if body.role is not None:
        if body.role not in Role.ALL:
            raise HTTPException(400, f"role must be one of {Role.ALL}")
        u.role = body.role
    if body.auth_mode is not None:
        if body.auth_mode not in AuthMode.ALL:
            raise HTTPException(400, f"auth_mode must be one of {AuthMode.ALL}")
        if body.auth_mode != AuthMode.TOTP and u.auth_mode == AuthMode.TOTP:
            _clear_totp(u)
        if body.auth_mode == AuthMode.TOTP and u.auth_mode != AuthMode.TOTP:
            _provision_totp(u)
        u.auth_mode = body.auth_mode
    if body.email is not None:
        u.email = str(body.email)
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.password:
        _check_password(body.password)
        u.password_hash = hash_password(body.password)
    # Don't strand a user in OTP mode with no delivery address.
    if u.auth_mode == AuthMode.OTP and not u.email:
        raise HTTPException(400, "This user uses OTP — an email is required.")
    await db.commit()
    await db.refresh(u)
    return _user_out(u)


@router.post("/users/{user_id}/auth-mode", response_model=UserOut)
async def switch_auth_mode(user_id: str, mode: str, db: AsyncSession = Depends(get_db)):
    if mode not in AuthMode.ALL:
        raise HTTPException(400, f"mode must be one of {AuthMode.ALL}")
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")
    if mode == AuthMode.OTP and not u.email:
        raise HTTPException(400, "Assign an email before switching this user to OTP")
    if mode != AuthMode.TOTP and u.auth_mode == AuthMode.TOTP:
        _clear_totp(u)
    if mode == AuthMode.TOTP and u.auth_mode != AuthMode.TOTP:
        _provision_totp(u)
    u.auth_mode = mode
    await db.commit()
    await db.refresh(u)
    return _user_out(u)


@router.post("/users/{user_id}/totp-setup", response_model=TotpSetupOut)
async def totp_setup(user_id: str, db: AsyncSession = Depends(get_db)):
    """Generate or reset authenticator secret and return a one-time QR setup payload."""
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")
    if u.auth_mode != AuthMode.TOTP:
        raise HTTPException(400, "User is not on authenticator mode")
    out = _provision_totp(u, reset=True)
    await db.commit()
    return out


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(400, "You cannot delete your own account")
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(404, "User not found")
    await db.delete(u)
    await db.commit()
    return {"deleted": user_id}


# ----------------------------- config (read-only — .env is source of truth) -----
@router.get("/config/status", response_model=list[AiStatusItem])
async def config_status(db: AsyncSession = Depends(get_db)):
    """Resolved OpenAI models and key status from .env (read-only)."""
    del db
    from app.core.config import settings
    from app.services.extraction.vision_client import model_for

    key = (settings.openai_api_key or "").strip().lower()
    has_key = bool(key) and key not in ("change-me", "missing")
    engine = (settings.extraction_engine or "mock").strip().lower()
    provider = (settings.llm_provider or "openai").strip().lower()
    extraction_note = None
    if engine != "vision":
        extraction_note = f"EXTRACTION_ENGINE={engine} — vision LLM disabled; using mock/deterministic engine."

    return [
        AiStatusItem(
            kind="extraction",
            label="Vision extraction (Extract Email, Upload, chat uploads)",
            provider=provider,
            model=model_for(provider),
            has_key=has_key,
            note=extraction_note,
        ),
        AiStatusItem(
            kind="agent",
            label="Agentic chat",
            provider=provider,
            model=settings.agent_chat_model or "gpt-4o-mini",
            has_key=has_key,
            note=None,
        ),
    ]


# ----------------------------- month calendars -----------------------------
def _calendar_out(c: MonthCalendar) -> MonthCalendarOut:
    return MonthCalendarOut(
        id=c.id, month=c.month, year=c.year,
        weekend_weekdays=c.weekend_weekdays or [],
        public_holidays=c.public_holidays or [],
        created_at=c.created_at, updated_at=c.updated_at,
    )


@calendars_router.get("/calendars", response_model=list[MonthCalendarOut])
async def list_calendars(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(MonthCalendar).order_by(MonthCalendar.year.desc(), MonthCalendar.month.desc())
    )).scalars().all()
    return [_calendar_out(c) for c in rows]


@calendars_router.put("/calendars", response_model=MonthCalendarOut)
async def upsert_calendar(body: MonthCalendarIn, db: AsyncSession = Depends(get_db)):
    """Create or replace the calendar for this (month, year) — one row per period."""
    if not (1 <= body.month <= 12):
        raise HTTPException(400, "month must be 1-12")
    row = (await db.execute(select(MonthCalendar).where(
        MonthCalendar.month == body.month, MonthCalendar.year == body.year
    ))).scalar_one_or_none()
    if row is None:
        row = MonthCalendar(month=body.month, year=body.year)
        db.add(row)
    row.weekend_weekdays = list(dict.fromkeys(body.weekend_weekdays))
    row.public_holidays = [h.model_dump() for h in body.public_holidays]
    await db.commit()
    await db.refresh(row)
    return _calendar_out(row)


@calendars_router.delete("/calendars/{calendar_id}")
async def delete_calendar(calendar_id: str, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(MonthCalendar).where(
        MonthCalendar.id == calendar_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Calendar not found")
    await db.delete(row)
    await db.commit()
    return {"deleted": calendar_id}


# ----------------------------- extraction debug runs -----------------------
def _debug_summary(r: ExtractionDebugRun) -> DebugRunSummary:
    return DebugRunSummary(
        id=r.id, created_at=r.created_at, source_kind=r.source_kind,
        source_id=r.source_id, thread_key=r.thread_key, subject=r.subject,
        model=r.model, calls=r.calls, reused_sheets=r.reused_sheets,
        n_pass1_calls=len(r.pass1_calls or []), n_pass2_calls=len(r.pass2_calls or []),
        n_dropped=len(r.dropped_items or []), n_sheets=len(r.sheets or []),
        n_errors=len(r.errors or []),
    )


@router.get("/debug/runs", response_model=list[DebugRunSummary])
async def list_debug_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(ExtractionDebugRun)
        .order_by(ExtractionDebugRun.created_at.desc())
        .limit(limit).offset(offset)
    )).scalars().all()
    return [_debug_summary(r) for r in rows]


@router.get("/debug/runs/{run_id}", response_model=DebugRunOut)
async def get_debug_run(run_id: str, db: AsyncSession = Depends(get_db)):
    r = (await db.execute(select(ExtractionDebugRun).where(
        ExtractionDebugRun.id == run_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Debug run not found")
    return DebugRunOut(
        **_debug_summary(r).model_dump(),
        pass1_calls=r.pass1_calls or [], pass2_calls=r.pass2_calls or [],
        dropped_items=r.dropped_items or [], triage=r.triage or [],
        sheets=r.sheets or [], errors=r.errors or [],
    )


@router.get("/debug/image")
async def get_debug_image(rel_path: str = Query(...)):
    """Serve a dropped-item's full-resolution image, saved under the same
    storage the pipeline's raw retry copies use (see raw_store.py) —
    rel_path always starts with 'debug/', never any other pipeline id."""
    if not rel_path.startswith("debug/"):
        raise HTTPException(400, "Not a debug image path")
    from app.services.pipeline import raw_store
    data = raw_store.read_raw(rel_path)
    if not data:
        raise HTTPException(404, "Image not found")
    return Response(content=data, media_type="image/jpeg")


@router.delete("/debug/runs")
async def clear_debug_runs(db: AsyncSession = Depends(get_db)):
    """Bulk-purge every debug run and its stored images — for when you're
    done testing (see the module docstring: this is a temporary aid, not a
    permanent audit log).

    Images were saved via raw_store.save_raw("debug/<run_id>", ...), but
    raw_store.delete_raw() assumes a single-segment pipeline id (it only
    strips the FIRST path segment for local storage, and deletes exactly one
    S3 key) — neither matches this nested "debug/<run_id>" layout, so the
    whole debug/ tree is removed directly here for local storage instead.
    (S3-backed deployments: this clears the DB rows; the S3 objects under
    the debug/ prefix are left for the bucket's own lifecycle rules — not
    implemented here since this app's storage_provider is local.)"""
    import shutil

    from app.core.config import settings

    count = (await db.execute(select(func.count()).select_from(ExtractionDebugRun))).scalar_one()
    await db.execute(delete(ExtractionDebugRun))
    await db.commit()

    debug_dir = settings.pipeline_raw_path / "debug"
    shutil.rmtree(debug_dir, ignore_errors=True)
    return {"deleted": count}


