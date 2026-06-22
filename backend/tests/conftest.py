"""
Shared test fixtures.

Tests run against PostgreSQL (the only supported database) — set
TEST_DATABASE_URL or it defaults to a local `timesheet_test` DB. Tables are
dropped + recreated at session start for isolation, the default admin is
seeded, and an httpx AsyncClient is bound to the ASGI app. Celery runs eager and
the cache uses its in-memory fallback, so no Redis/worker is needed.
"""
from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio

# ---- isolate every test session from the dev DB/storage BEFORE app import ----
_TMP = tempfile.mkdtemp(prefix="ts_tests_")
_TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet_test",
)
os.environ.update(
    ENVIRONMENT="dev",
    DATABASE_URL=_TEST_DB,
    DB_NULLPOOL="true",                    # fresh connection per op (asyncpg + per-test loops)
    STORAGE_ROOT=f"{_TMP}/storage",
    PIPELINE_RAW_ROOT=f"{_TMP}/pipeline_raw",
    AUTH_ENABLED="true",
    CELERY_TASK_ALWAYS_EAGER="true",
    CACHE_ENABLED="false",                 # use in-memory cache fallback
    JWT_SECRET="test-secret-key-please-32-bytes-minimum-length-ok",
    DEFAULT_ADMIN_USERNAME="admin",
    DEFAULT_ADMIN_PASSWORD="admin",
    DEFAULT_ADMIN_EMAIL="admin@example.com",
    EXTRACTION_ENGINE="mock",
    EMAIL_PROVIDER="mock",
    FINGERPRINT_REQUIRED="true",
    OTP_RESEND_COOLDOWN_SECONDS="0",       # don't sleep in tests
    # high default limits so the shared admin login isn't throttled; the
    # dedicated rate-limit test lowers the limit locally.
    LOGIN_RATE_MAX="1000",
    OTP_VERIFY_RATE_MAX="1000",
)

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_db():
    # clean slate: drop + recreate all tables on the test database
    from app.core.database import Base, engine
    import app.models  # noqa: F401  (register tables)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await init_db()
    from app.core.database import SessionLocal
    from app.seed.seed_admin import seed_admin
    async with SessionLocal() as db:
        await seed_admin(db)
    yield


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"User-Agent": "pytest", "X-Fingerprint": "fp-test"}) as c:
        yield c


@pytest_asyncio.fixture
async def admin_token(client) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "authenticated"
    return data["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Fingerprint": "fp-test"}
