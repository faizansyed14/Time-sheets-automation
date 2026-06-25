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

_TMP = tempfile.mkdtemp(prefix="ts_tests_")
_TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://timesheet:timesheet@localhost:5432/timesheet_test",
)
os.environ.update(
    ENVIRONMENT="dev",
    DATABASE_URL=_TEST_DB,
    DB_NULLPOOL="true",
    STORAGE_ROOT=f"{_TMP}/storage",
    PIPELINE_RAW_ROOT=f"{_TMP}/pipeline_raw",
    AUTH_ENABLED="true",
    CELERY_TASK_ALWAYS_EAGER="true",
    CACHE_ENABLED="false",
    JWT_SECRET="test-secret-key-please-32-bytes-minimum-length-ok",
    DEFAULT_ADMIN_USERNAME="admin",
    DEFAULT_ADMIN_PASSWORD="admin",
    DEFAULT_ADMIN_EMAIL="admin@example.com",
    EXTRACTION_ENGINE="mock",
    EMAIL_PROVIDER="mock",
    FINGERPRINT_REQUIRED="true",
    OTP_RESEND_COOLDOWN_SECONDS="0",
    LOGIN_RATE_MAX="1000",
    OTP_VERIFY_RATE_MAX="1000",
    TOTP_VERIFY_RATE_MAX="1000",
    CAPTCHA_RATE_MAX="1000",
    CAPTCHA_VERIFY_RATE_MAX="1000",
)

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_db():
    from app.core.database import Base, engine
    import app.models  # noqa: F401
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


async def _fetch_captcha(client):
    from app.core.cache import cache
    cap = await client.get("/api/v1/auth/captcha")
    assert cap.status_code == 200, cap.text
    cid = cap.headers["x-captcha-id"]
    answer = await cache.get(f"captcha:{cid}")
    assert answer
    return cid, answer


async def _login(client, username: str, password: str):
    cid, answer = await _fetch_captcha(client)
    return await client.post("/api/v1/auth/login", json={
        "username": username,
        "password": password,
        "captcha_id": cid,
        "captcha_answer": answer,
    })


async def login_2fa(client, username: str, password: str) -> str:
    """Full login: credentials + CAPTCHA, then second factor if required."""
    import pyotp
    from app.core.database import SessionLocal
    from app.models.auth import User
    from app.services.auth import totp as totp_svc
    from sqlalchemy import select

    r = await _login(client, username, password)
    assert r.status_code == 200, r.text
    data = r.json()
    if data["status"] == "authenticated":
        return data["access_token"]
    if data["status"] == "otp_required":
        v = await client.post("/api/v1/auth/verify-otp",
                              json={"login_token": data["login_token"], "code": data["debug_otp"]})
    elif data["status"] in ("totp_required", "totp_enrollment_required"):
        async with SessionLocal() as db:
            user = (await db.execute(
                select(User).where(User.username == username))).scalar_one_or_none()
            secret = totp_svc.decrypt_secret(user.totp_secret_enc if user else None)
        code = pyotp.TOTP(secret).now()
        v = await client.post("/api/v1/auth/verify-totp",
                              json={"login_token": data["login_token"], "code": code})
    else:
        raise AssertionError(f"unexpected login status: {data}")
    assert v.status_code == 200, v.text
    return v.json()["access_token"]


@pytest_asyncio.fixture
async def admin_token(client) -> str:
    return await login_2fa(client, "admin", "admin")


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Fingerprint": "fp-test"}
