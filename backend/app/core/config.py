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
    # Deployment environment: "dev" | "prod". Loaded from .env / .env.<env>.
    environment: str = "dev"

    # ===================== Database (PostgreSQL only) =====================
    # Every environment uses Postgres. For AWS RDS just point this at the RDS
    # endpoint — no code changes:
    #   postgresql+asyncpg://USER:PASS@my-db.xxxx.rds.amazonaws.com:5432/timesheet
    database_url: str = "postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # Schema bootstrap strategy.
    #   True  (default) -> create any missing tables on startup via SQLAlchemy
    #                      (handy for local quick-start and the test suite).
    #   False           -> Alembic is the single source of truth; run
    #                      `alembic upgrade head` before the app boots. Docker
    #                      and AWS/RDS deployments set AUTO_CREATE_TABLES=false.
    auto_create_tables: bool = True
    # Use NullPool (a fresh connection per operation). Enabled in the test suite
    # so asyncpg connections never cross pytest-asyncio's per-test event loops.
    db_nullpool: bool = False

    # Where pulled timesheets + LLM results are filed.
    # local: storage/<Manager>/<Employee>/<Month-Year>/<files>
    storage_root: str = str(BACKEND_ROOT / "storage")

    # Where the pipeline keeps a private copy of each original file so a failed
    # file can be retried. This lives OUTSIDE storage_root so it never shows up
    # in the File Vault browser. (Local-disk providers only.)
    pipeline_raw_root: str = str(BACKEND_ROOT / "data" / "pipeline_raw")
    # Retry copies are normally deleted as soon as a file succeeds; this caps how
    # long a STILL-failed file's original is kept before it is purged, so the
    # raw store can never grow without bound. 0 disables the age purge.
    pipeline_raw_retention_days: int = 60

    # Which email provider to use: "mock" now, "graph" later.
    email_provider: str = "mock"

    # Which file store to use: "local" | "s3" | "onedrive".
    # Switch to AWS S3 by setting STORAGE_PROVIDER=s3 + the S3_* values below.
    storage_provider: str = "local"

    # ----- AWS S3 storage (storage_provider="s3") -----
    s3_bucket: str = ""
    s3_prefix: str = "timesheets"            # key prefix (acts as the root folder)
    # Raw retry copies (originals kept only so a failed file can be retried) are
    # stored under  <s3_prefix>/<s3_raw_prefix>/  on S3 — i.e. a folder INSIDE
    # timesheets/ so it's visible in the S3 console and covered by a policy scoped
    # to timesheets/*. The leading "_" keeps it hidden from the File Vault browser
    # (which skips folders starting with "_" or ".").
    s3_raw_prefix: str = "_pipeline-raw"
    s3_region: str = "us-east-1"
    aws_access_key_id: str | None = None     # omit on EC2/ECS to use the IAM role
    aws_secret_access_key: str | None = None
    s3_endpoint_url: str | None = None       # for MinIO / S3-compatible stores

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
    vision_image_detail: str = "high"   # low | high — used for SCANS/photos
    validation_model: str = "gpt-4o-mini"
    enable_text_validation: bool = True

    # ----- Cost / accuracy tuning (all admin-editable) -----
    # Render PDFs at a lower DPI for the LLM: vision models gain nothing above
    # ~150 DPI, and a smaller image = fewer tokens (esp. at high detail).
    pdf_render_dpi: int = 150
    # Born-digital files already provide an authoritative text layer, so the
    # image is only layout context — send it at "low" detail (≈5–10× cheaper).
    # Scans/photos (no text layer) keep `vision_image_detail`.
    vision_adaptive_detail: bool = True
    # Force valid JSON from supported OpenAI models (response_format json_object)
    # so a stray markdown fence / trailing text can't break parsing.
    vision_json_mode: bool = True
    # Skip the LLM entirely for clean digital sheets whose text layer parses
    # confidently (deterministic parser) — biggest cost saver, opt-in.
    extraction_prefer_deterministic: bool = False
    # Optional OCR "reader" to give scans/photos a text layer (none|tesseract).
    # tesseract runs locally (pytesseract + tesseract-ocr) and is completely free.
    ocr_provider: str = "none"
    # Model for the Agentic Chat assistant (text → safe DB actions).
    agent_chat_model: str = "gpt-4o-mini"

    # CORS for the Vite dev server.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ----- Microsoft Graph (email_provider="graph" + OTP delivery) -----
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_mailbox: str = ""          # e.g. timesheets@yourcompany.com
    graph_folder: str = "Inbox"      # folder to watch
    graph_otp_sender: str = ""       # mailbox OTP emails are sent FROM (defaults to graph_mailbox)

    # ===================== Infrastructure =====================
    # Redis — caching + Celery broker/result backend + rate-limit windows.
    # If unreachable the app transparently falls back to an in-process cache.
    redis_url: str = "redis://localhost:6379/0"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300

    # Celery — background work (OTP email, async ingestion). In dev/tests with
    # CELERY_TASK_ALWAYS_EAGER=true tasks run inline so no worker is required.
    celery_broker_url: str = ""      # defaults to redis_url
    celery_result_backend: str = ""  # defaults to redis_url
    celery_task_always_eager: bool = True

    # ---- Scheduled background jobs (Celery beat) — intervals configurable ----
    # How often the S3 / disk pipeline-raw retry copies are purged. Default once
    # a day. Set in hours; e.g. 24 = daily, 12 = twice a day, 168 = weekly.
    pipeline_raw_purge_interval_hours: float = 24.0

    # ===================== Auth / Security =====================
    auth_enabled: bool = True
    jwt_secret: str = "change-me-in-prod-please-use-a-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 8        # session length
    login_token_ttl_minutes: int = 10             # short-lived "password ok, awaiting 2FA" token

    # Default admin (seeded on first boot). Override in .env for production.
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"
    default_admin_email: str = "admin@example.com"

    # OTP lifecycle
    otp_length: int = 6
    otp_ttl_seconds: int = 300                    # expiry
    otp_max_attempts: int = 5                     # wrong-code attempts before lockout
    otp_resend_limit: int = 3                     # resends allowed within a login session
    otp_resend_cooldown_seconds: int = 30

    # Rate limiting (sliding-window, per identifier+route)
    login_rate_max: int = 10                      # attempts
    login_rate_window_seconds: int = 300          # per 5 min
    otp_verify_rate_max: int = 20
    otp_verify_rate_window_seconds: int = 300
    # CAPTCHA issuance throttle: at most N fresh/refreshed captchas per IP per
    # window; the (N+1)th is blocked until the window slides (≈ a 300s lockout).
    captcha_rate_max: int = 10
    captcha_rate_window_seconds: int = 300
    # CAPTCHA answer-verification throttle (brute-force guard on the answer).
    captcha_verify_rate_max: int = 20
    captcha_verify_rate_window_seconds: int = 300

    # CAPTCHA
    captcha_length: int = 6
    captcha_ttl_seconds: int = 180
    totp_issuer: str = "TimeSight"
    totp_verify_rate_max: int = 20
    totp_verify_rate_window_seconds: int = 300

    # Device fingerprint must stay consistent through a login flow
    fingerprint_required: bool = True

    @property
    def is_prod(self) -> bool:
        return self.environment.lower().startswith("prod")

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

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
