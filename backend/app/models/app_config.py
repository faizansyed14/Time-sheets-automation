"""
AppConfig — a dedicated, isolated key/value table for runtime configuration the
admin manages from the UI (prompts, AI provider + API keys, model controls).

It deliberately does NOT touch the existing schema. Values are JSON-encoded;
secret values (API keys) are encrypted at rest (see core.crypto). A runtime
overlay (core.runtime_config) layers these over the .env defaults so changing a
setting here takes effect without a redeploy.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConfigCategory:
    PROVIDER = "provider"     # ai provider + api keys + base urls
    MODEL = "model"           # extraction/chat models + tuning knobs
    PROMPT = "prompt"         # editable prompts
    GENERAL = "general"


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-encoded (encrypted if secret)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str] = mapped_column(String, default=ConfigCategory.GENERAL, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
