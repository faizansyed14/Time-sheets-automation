"""
Application configuration.

All tunables live here so behaviour can change via environment variables
without touching code. In production you would back these with a real .env
file (see .env.example).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = backend/  (two levels up from this file: app/core/config.py)
BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Timesheet Intelligence Portal"
    api_prefix: str = "/api/v1"

    # SQLite by default so the app runs with zero external services.
    # For production swap to Postgres, e.g.
    #   postgresql+asyncpg://user:pass@host:5432/timesheets
    database_url: str = f"sqlite+aiosqlite:///{BACKEND_ROOT / 'data' / 'app.db'}"

    # Where pulled timesheets + LLM results are filed on disk.
    # Structure:  storage/<employee_name>/<Month-Year>/<files>
    storage_root: str = str(BACKEND_ROOT / "storage")

    # Where the pipeline keeps a private copy of each original file so a failed
    # file can be retried. This lives OUTSIDE storage_root so it never shows up
    # in the File Vault browser.
    pipeline_raw_root: str = str(BACKEND_ROOT / "data" / "pipeline_raw")

    # Which email provider to use: "mock" now, "graph" later.
    email_provider: str = "mock"

    # Which file store to use: "local" now, "onedrive" later.
    storage_provider: str = "local"

    # Which extraction engine to use: "mock" now, "vision" for your real LLM.
    extraction_engine: str = "mock"

    # ----- Real vision LLM (used when extraction_engine="vision") -----
    # OpenAI-compatible vision (GPT-4o / GPT-5.4 / GPT-4.1).
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    openai_timeout: int = 120
    # Optional vLLM (Qwen etc.) for non-GPT models.
    vllm_api_key: str | None = None
    vllm_base_url: str = "https://myvllmserver.duckdns.org"
    vllm_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    vllm_max_tokens: int = 4096
    vllm_temperature: float = 0.0
    vllm_timeout: int = 90
    # Runtime model choices (same names your project uses).
    extraction_model: str = "gpt-4o"
    vision_image_detail: str = "high"   # low | high
    validation_model: str = "gpt-4o-mini"
    enable_text_validation: bool = True

    # CORS for the Vite dev server.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ----- Microsoft Graph (only used when email_provider="graph") -----
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_mailbox: str = ""          # e.g. timesheets@yourcompany.com
    graph_folder: str = "Inbox"      # folder to watch

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_root)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def pipeline_raw_path(self) -> Path:
        p = Path(self.pipeline_raw_root)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
