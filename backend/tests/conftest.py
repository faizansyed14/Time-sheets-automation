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
    # Isolate tests from docker .env — OTP must not hit Graph; chat must not call LLMs.
    GRAPH_TENANT_ID="",
    GRAPH_CLIENT_ID="",
    GRAPH_CLIENT_SECRET="",
    GRAPH_MAILBOX="",
    GRAPH_OTP_SENDER="",
    # "missing" is a non-empty key so require_vision_configured() doesn't
    # block the thread pipeline in tests — the vision call itself is
    # monkeypatched per-test (see mock_vision_calls below), so nothing ever
    # reaches the network. It's also the sentinel llm_provider.active_config
    # treats as "no key configured", so the agentic-chat no-key-degrades test
    # still sees has_key=False as it did before this pipeline needed a key.
    OPENAI_API_KEY="missing",
    LLM_PROVIDER="openrouter",
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
    """Step 1 of the login: username + password only. The response says which
    single challenge (captcha / otp / totp) finishes the sign-in."""
    return await client.post("/api/v1/auth/login", json={
        "username": username,
        "password": password,
    })


async def login_2fa(client, username: str, password: str) -> str:
    """Full login: credentials, then the user's ONE challenge (never stacked)."""
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
    if data["status"] == "captcha_required":
        cid, answer = await _fetch_captcha(client)
        v = await client.post("/api/v1/auth/verify-captcha",
                              json={"login_token": data["login_token"],
                                    "captcha_id": cid, "answer": answer})
    elif data["status"] == "otp_required":
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


@pytest.fixture
def mock_vision_calls(monkeypatch):
    """Script the two-pass vision pipeline without any network call.

    Usage: `install = mock_vision_calls; install([pass1_reply, pass2_reply])`
    Each call to vision_client.chat_call pops the next scripted reply, in
    order (pass 1 may issue several batched calls before pass 2's).
    Raises loudly if a test's pipeline run needs more calls than scripted,
    rather than hanging on a real HTTP request.

    Queue an `Exception` instance instead of a dict to simulate that specific
    call failing (its own internal retries already exhausted) — for testing
    that ONE batch failing doesn't take down every other batch's results."""
    from app.services.extraction import vision_client

    def install(responses: list[dict]):
        queue = list(responses)

        async def _fake_chat_call(system_prompt, content_blocks, model, api_key,
                                  *, label="", retries=3, force_json=True, enable_thinking=True):
            if not queue:
                raise AssertionError(f"mock_vision_calls exhausted — unexpected extra call ({label})")
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        monkeypatch.setattr(vision_client, "chat_call", _fake_chat_call)
        monkeypatch.setattr("app.services.extract_email.triage_prompt.chat_call", _fake_chat_call)
        monkeypatch.setattr("app.services.extract_email.thread_prompt.chat_call", _fake_chat_call)
        return queue

    return install
