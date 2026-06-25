"""
Runtime configuration service.

The admin edits AI settings (provider, API keys, models, prompts, toggles) from
the UI; they land in the `app_config` table (secrets encrypted). This service:

  - reads/writes those values (decrypting secrets on read for internal use,
    masking them on read for the API),
  - overlays them on top of the .env `settings` so the rest of the app can call
    `get_setting("vision_image_detail")` and get the admin's value if set, else
    the env default,
  - caches the overlay in Redis/in-memory and busts it on write.

Keys are a small, explicit allow-list so the UI can't write arbitrary settings.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache
from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.models.app_config import AppConfig, ConfigCategory

_CACHE_KEY = "appconfig:overlay"

# key -> (category, is_secret, env_attr_or_None, default)
CONFIG_KEYS: dict[str, dict] = {
    # provider
    "ai_provider":        {"category": ConfigCategory.PROVIDER, "secret": False, "env": None, "default": "openai"},
    "openai_api_key":     {"category": ConfigCategory.PROVIDER, "secret": True,  "env": "openai_api_key", "default": ""},
    "openai_base_url":    {"category": ConfigCategory.PROVIDER, "secret": False, "env": "openai_base_url", "default": "https://api.openai.com/v1"},
    "deepseek_api_key":   {"category": ConfigCategory.PROVIDER, "secret": True,  "env": None, "default": ""},
    "deepseek_base_url":  {"category": ConfigCategory.PROVIDER, "secret": False, "env": None, "default": "https://api.deepseek.com/v1"},
    # model controls
    "extraction_engine":  {"category": ConfigCategory.MODEL, "secret": False, "env": "extraction_engine", "default": "mock"},
    "extraction_model":   {"category": ConfigCategory.MODEL, "secret": False, "env": "extraction_model", "default": "gpt-4o"},
    "vision_image_detail":{"category": ConfigCategory.MODEL, "secret": False, "env": "vision_image_detail", "default": "high"},
    "validation_model":   {"category": ConfigCategory.MODEL, "secret": False, "env": "validation_model", "default": "gpt-4o-mini"},
    "enable_text_validation": {"category": ConfigCategory.MODEL, "secret": False, "env": "enable_text_validation", "default": True},
    # cost / accuracy tuning
    "pdf_render_dpi":         {"category": ConfigCategory.MODEL, "secret": False, "env": "pdf_render_dpi", "default": 150},
    "vision_adaptive_detail":{"category": ConfigCategory.MODEL, "secret": False, "env": "vision_adaptive_detail", "default": True},
    "vision_json_mode":      {"category": ConfigCategory.MODEL, "secret": False, "env": "vision_json_mode", "default": True},
    "extraction_prefer_deterministic": {"category": ConfigCategory.MODEL, "secret": False, "env": "extraction_prefer_deterministic", "default": False},
    "ocr_provider":          {"category": ConfigCategory.MODEL, "secret": False, "env": "ocr_provider", "default": "none"},
    # prompts (overlaid onto parser.* at read time)
    "system_prompt":      {"category": ConfigCategory.PROMPT, "secret": False, "env": None, "default": ""},
    "extraction_prompt":  {"category": ConfigCategory.PROMPT, "secret": False, "env": None, "default": ""},
    "summary_prompt":     {"category": ConfigCategory.PROMPT, "secret": False, "env": None, "default": ""},
}

SECRET_MASK = "••••••••"


def _env_default(key: str):
    meta = CONFIG_KEYS[key]
    if meta["env"]:
        return getattr(settings, meta["env"], meta["default"])
    return meta["default"]


async def _load_overlay(db: AsyncSession) -> dict:
    """All stored config as {key: decoded_value} (secrets decrypted)."""
    rows = (await db.execute(select(AppConfig))).scalars().all()
    out: dict = {}
    for r in rows:
        if r.key not in CONFIG_KEYS:
            continue
        raw = r.value
        if r.is_secret and raw:
            raw = decrypt(raw)
        try:
            out[r.key] = json.loads(raw) if raw is not None else None
        except Exception:
            out[r.key] = raw
    return out


async def get_overlay(db: AsyncSession) -> dict:
    """Effective config = env defaults <- stored overrides, cached."""
    cached = await cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    stored = await _load_overlay(db)
    effective = {k: stored.get(k, _env_default(k)) for k in CONFIG_KEYS}
    await cache.set(_CACHE_KEY, effective, ttl=settings.cache_ttl_seconds)
    return effective


async def get_setting(db: AsyncSession, key: str):
    if key not in CONFIG_KEYS:
        raise KeyError(key)
    return (await get_overlay(db)).get(key, _env_default(key))


async def set_settings(db: AsyncSession, values: dict, updated_by: str | None = None) -> None:
    """Upsert a batch of config values. Secrets are encrypted; a SECRET_MASK
    value means 'leave unchanged'. Busts the overlay cache."""
    for key, val in values.items():
        if key not in CONFIG_KEYS:
            continue
        meta = CONFIG_KEYS[key]
        if meta["secret"] and (val == SECRET_MASK or val is None):
            continue  # don't overwrite a stored secret with the mask
        row = (await db.execute(select(AppConfig).where(AppConfig.key == key))).scalar_one_or_none()
        encoded = json.dumps(val)
        if meta["secret"]:
            encoded = encrypt(encoded)
        if row:
            row.value, row.is_secret, row.category, row.updated_by = encoded, meta["secret"], meta["category"], updated_by
        else:
            db.add(AppConfig(key=key, value=encoded, is_secret=meta["secret"],
                             category=meta["category"], updated_by=updated_by))
    await db.commit()
    await cache.delete(_CACHE_KEY)
    apply_overlay_to_runtime(await _load_overlay(db))


def apply_overlay_to_runtime(stored: dict) -> None:
    """Push stored overrides onto the live process so existing code that reads
    `settings.*` and `parser.*` immediately reflects admin changes (no restart)."""
    for key, meta in CONFIG_KEYS.items():
        if meta["env"] and key in stored and stored[key] is not None:
            setattr(settings, meta["env"], stored[key])
    try:
        from app.services.extraction import parser
        parser.set_prompt_overrides({
            "system": stored.get("system_prompt") or None,
            "extraction": stored.get("extraction_prompt") or None,
            "summary": stored.get("summary_prompt") or None,
        })
    except Exception:
        pass


async def load_and_apply(db: AsyncSession) -> None:
    """Called on startup to apply any persisted config to the live process."""
    apply_overlay_to_runtime(await _load_overlay(db))


async def public_view(db: AsyncSession) -> list[dict]:
    """Config for the admin UI — secrets masked, grouped with metadata."""
    overlay = await get_overlay(db)
    out = []
    for key, meta in CONFIG_KEYS.items():
        val = overlay.get(key)
        if meta["secret"]:
            val = SECRET_MASK if val else ""
        out.append({"key": key, "value": val, "category": meta["category"], "is_secret": meta["secret"]})
    return out


async def reveal_secret(db: AsyncSession, key: str) -> str:
    """Return the *plaintext* value of a secret config key (admin-only).

    Backs the "show key" toggle in AI Settings so an admin can confirm exactly
    which key is in use. Returns "" when the key isn't a known secret or nothing
    is stored yet."""
    meta = CONFIG_KEYS.get(key)
    if not meta or not meta["secret"]:
        raise KeyError(key)
    overlay = await get_overlay(db)          # secrets are decrypted in the overlay
    return overlay.get(key) or ""
