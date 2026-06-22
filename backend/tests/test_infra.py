"""Infrastructure: cache, sliding window, Celery eager, LangChain factory."""
import base64

import pytest

from tests.conftest import auth_headers


async def test_cache_set_get_ttl_incr():
    from app.core.cache import cache
    await cache.set("k1", {"a": 1}, ttl=10)
    assert await cache.get("k1") == {"a": 1}
    assert await cache.exists("k1") is True
    await cache.delete("k1")
    assert await cache.get("k1") is None
    assert await cache.incr("counter") == 1
    assert await cache.incr("counter") == 2


async def test_sliding_window_counts():
    from app.core.cache import cache
    n = 0
    for _ in range(5):
        n = await cache.sliding_window_add("unit:test:sw", 60)
    assert n == 5


async def test_celery_task_eager_runs_inline():
    # eager mode -> .delay returns a finished EagerResult
    from app.services.tasks import send_otp_email_task
    res = send_otp_email_task.delay("dev@example.com", "123456")
    assert res.get() is True  # dev sender returns True (logs the code)


async def test_celery_process_upload_eager(client, admin_token):
    # build a tiny text "timesheet" the mock engine can read
    from app.services.tasks import process_upload_task
    text = ("MONTHLY TIMESHEET\nEmployee Name: Infra Tester\nEmployee ID: INF-1\n"
            "Month: March 2026\n2026-03-03 Annual Leave\n").encode()
    res = process_upload_task.delay("infra.pdf", "text/plain", base64.b64encode(text).decode())
    out = res.get()
    assert "pipeline_id" in out


async def test_langchain_model_factory_builds():
    # building the model object must not require a live API call
    from app.core.database import SessionLocal
    from app.services.llm import provider
    async with SessionLocal() as db:
        model = await provider.get_chat_model(db, kind="extraction", provider="deepseek")
    assert model.__class__.__name__ == "ChatOpenAI"


async def test_health_reports_environment(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "environment" in body and "auth_enabled" in body
