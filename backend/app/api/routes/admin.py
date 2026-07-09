"""
Admin routes (RBAC: admin only).

Users:
  GET    /admin/users                list
  POST   /admin/users                create (assign email for OTP, set role + auth_mode)
  PATCH  /admin/users/{id}           update (email, role, auth_mode, active, password)
  POST   /admin/users/{id}/auth-mode switch OTP <-> CAPTCHA
  DELETE /admin/users/{id}

Config (prompts, AI provider + keys, model controls):
  GET    /admin/config               current values (secrets masked)
  PUT    /admin/config               update a batch of values
  GET    /admin/config/reveal/{key}  plaintext of one secret (the "show" toggle)
  POST   /admin/config/test          live-test the configured provider
  GET    /admin/config/prompts/defaults  built-in prompt text (to pre-fill the editor)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_db
from app.core.security import hash_password
from app.models.auth import AuthMode, Role, User
from app.schemas.auth import (
    AdminUserCreate,
    AdminUserUpdate,
    AiStatusItem,
    ConfigItem,
    ConfigUpdate,
    ProviderTestIn,
    ProviderTestResult,
    TotpSetupOut,
    UserOut,
)
from app.services.auth import totp as totp_svc
from app.services.config import service as config_service
from app.services.llm import provider as llm_provider

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


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


# ----------------------------- config -----------------------------
@router.get("/config", response_model=list[ConfigItem])
async def get_config(db: AsyncSession = Depends(get_db)):
    return [ConfigItem(**c) for c in await config_service.public_view(db)]


@router.put("/config", response_model=list[ConfigItem])
async def update_config(body: ConfigUpdate, admin: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    bad = [k for k in body.values if k not in config_service.CONFIG_KEYS]
    if bad:
        raise HTTPException(400, f"Unknown config keys: {bad}")
    await config_service.set_settings(db, body.values, updated_by=admin.username)
    return [ConfigItem(**c) for c in await config_service.public_view(db)]


@router.get("/config/reveal/{key}")
async def reveal_config_secret(key: str, db: AsyncSession = Depends(get_db)):
    """Return the plaintext value of a secret key so the admin can confirm which
    key is in use (backs the "show" toggle in AI Settings)."""
    try:
        return {"key": key, "value": await config_service.reveal_secret(db, key)}
    except KeyError:
        raise HTTPException(400, f"'{key}' is not a revealable secret key")


@router.post("/config/test", response_model=ProviderTestResult)
async def test_config(body: ProviderTestIn, db: AsyncSession = Depends(get_db)):
    res = await llm_provider.test_provider(db, body.provider, body.prompt)
    return ProviderTestResult(**res)


@router.get("/config/status", response_model=list[AiStatusItem])
async def config_status(db: AsyncSession = Depends(get_db)):
    """The ACTUAL resolved provider + model for every AI call site, read the
    same way each call routes — each service picks its own provider, and the
    endpoint + key come from that provider."""
    overlay = await config_service.get_overlay(db)

    def _key_configured(provider: str) -> bool:
        key = ((overlay.get("openai_api_key") if provider == "openai"
                else overlay.get("vllm_api_key") if provider == "vllm"
                else overlay.get("deepseek_api_key")) or "").strip().lower()
        return bool(key) and key not in ("change-me", "missing")

    def _item(kind: str, label: str, provider_key: str, model_key: str,
              note: str | None = None) -> AiStatusItem:
        provider = str(overlay.get(provider_key) or "openai").lower()
        return AiStatusItem(kind=kind, label=label, provider=provider,
                            model=str(overlay.get(model_key) or ""),
                            has_key=_key_configured(provider), note=note)

    agent_provider = str(overlay.get("ai_provider") or "openai").lower()
    agent_note = None
    if agent_provider != "openai":
        agent_note = ("Agentic chat needs OpenAI-style tool calling; self-hosted "
                      "servers must run with --enable-auto-tool-choice and a "
                      "matching --tool-call-parser or chat will fail.")

    return [
        _item("extraction", "Vision extraction (Extract Email, per-file, approvals)",
              "vision_provider", "extraction_model"),
        _item("validation", "Validation / cross-check / summaries",
              "validation_provider", "validation_model"),
        _item("agent", "Agentic chat", "ai_provider", "agent_chat_model", note=agent_note),
    ]


@router.get("/config/prompts/defaults")
async def prompt_defaults():
    from app.services.extraction import parser
    return {
        "system_prompt": parser.SYSTEM_PROMPT,
        "extraction_prompt": parser.EXTRACTION_PROMPT,
        "summary_prompt": parser.SUMMARY_PROMPT,
    }
