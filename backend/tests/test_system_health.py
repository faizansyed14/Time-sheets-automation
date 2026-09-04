"""System health monitoring — LLM key/credit + Graph credential checks, the
alert-on-state-change rule, and full isolation from reminders/OTP.

Graph creds are blank in the test env (see conftest.py) — real Graph HTTP is
mocked directly here (monkeypatching httpx.get/post) rather than relying on
the "not configured" dev fallback, since the whole point of these tests is
exercising the actual check logic, not skipping it.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.models.system_health import HealthComponent, HealthStatus, SystemHealthCheck
from app.services.system_health import monitor
from tests.conftest import auth_headers


class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text or ""

    def json(self):
        return self._json


# --------------------------------------------------------------------- #
# _check_llm_sync
# --------------------------------------------------------------------- #
def test_llm_check_reports_not_applicable_when_no_key_configured(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "")
    result = monitor._check_llm_sync()
    assert result.applicable is False
    assert result.status == HealthStatus.UNKNOWN


def test_llm_check_openrouter_ok_with_plenty_of_credit(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "sk-or-test")
    monkeypatch.setattr(settings, "openai_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(
        200, {"data": {"limit": 100.0, "usage": 10.0}}))
    result = monitor._check_llm_sync()
    assert result.applicable and result.status == HealthStatus.OK


def test_llm_check_openrouter_still_ok_with_a_small_but_nonzero_balance(monkeypatch):
    """No "running low" tier — deliberately removed (no way to know how many
    dollars a given prompt will actually cost ahead of time, so a fixed
    dollar floor would be a guess, not a fact). Only exact exhaustion (below)
    or an invalid key count as not-ok."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "sk-or-test")
    monkeypatch.setattr(settings, "openai_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(
        200, {"data": {"limit": 100.0, "usage": 99.99}}))  # 1 cent remaining
    result = monitor._check_llm_sync()
    assert result.status == HealthStatus.OK


def test_llm_check_openrouter_down_when_exhausted(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "sk-or-test")
    monkeypatch.setattr(settings, "openai_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(
        200, {"data": {"limit": 100.0, "usage": 100.0}}))
    result = monitor._check_llm_sync()
    assert result.status == HealthStatus.DOWN


def test_llm_check_openrouter_down_on_401(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "sk-or-test")
    monkeypatch.setattr(settings, "openai_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(401))
    result = monitor._check_llm_sync()
    assert result.status == HealthStatus.DOWN


@pytest.mark.parametrize("status_code,expected", [
    # 429 here is reported DOWN (something's wrong, look into it), never as
    # a credit/quota guess — /models isn't billing-gated the way a real
    # completion call is, so a 429 here is far more likely a plain rate
    # limit than a quota signal.
    (200, HealthStatus.OK), (401, HealthStatus.DOWN), (429, HealthStatus.DOWN),
])
def test_llm_check_generic_openai_compatible(monkeypatch, status_code, expected):
    from app.core.config import settings
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.openai.com")
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr("httpx.get", lambda *a, **k: _FakeResponse(status_code))
    result = monitor._check_llm_sync()
    assert result.status == expected


# --------------------------------------------------------------------- #
# _check_graph_sync
# --------------------------------------------------------------------- #
def test_graph_check_reports_not_applicable_when_not_configured(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "graph_tenant_id", "")
    result = monitor._check_graph_sync()
    assert result.applicable is False and result.status == HealthStatus.UNKNOWN


def test_graph_check_ok_on_valid_token_exchange(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "graph_tenant_id", "tid")
    monkeypatch.setattr(settings, "graph_client_id", "cid")
    monkeypatch.setattr(settings, "graph_client_secret", "secret")
    monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResponse(200, {"access_token": "abc"}))
    result = monitor._check_graph_sync()
    assert result.status == HealthStatus.OK


def test_graph_check_down_on_expired_or_wrong_secret(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "graph_tenant_id", "tid")
    monkeypatch.setattr(settings, "graph_client_id", "cid")
    monkeypatch.setattr(settings, "graph_client_secret", "wrong-or-expired")
    monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResponse(
        401, {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret."}))
    result = monitor._check_graph_sync()
    assert result.status == HealthStatus.DOWN


# --------------------------------------------------------------------- #
# Graph client secret expiry — manually-configured date, layered on top of
# (never instead of) the live check above.
# --------------------------------------------------------------------- #
def _graph_ok(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "graph_tenant_id", "tid")
    monkeypatch.setattr(settings, "graph_client_id", "cid")
    monkeypatch.setattr(settings, "graph_client_secret", "secret")
    monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResponse(200, {"access_token": "abc"}))


def test_graph_expiry_far_in_the_future_stays_ok(monkeypatch):
    from app.core.config import settings
    _graph_ok(monkeypatch)
    future = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=400)).isoformat()
    monkeypatch.setattr(settings, "graph_client_secret_expires_on", future)
    result = monitor._check_graph_sync()
    assert result.status == HealthStatus.OK


def test_graph_expiry_within_warn_window_is_degraded(monkeypatch):
    from app.core.config import settings
    _graph_ok(monkeypatch)
    monkeypatch.setattr(settings, "system_health_graph_expiry_warn_days", 30)
    soon = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=10)).isoformat()
    monkeypatch.setattr(settings, "graph_client_secret_expires_on", soon)
    result = monitor._check_graph_sync()
    assert result.status == HealthStatus.DEGRADED
    assert soon in result.detail


def test_graph_expiry_already_passed_still_reports_degraded_not_silently_ok(monkeypatch):
    """The live check can still legitimately succeed even past a
    configured expiry (e.g. someone renewed it in Azure but forgot to
    update this date) — the real check result must not be overridden to
    DOWN just from the date, but this must not go silently unreported
    either."""
    from app.core.config import settings
    _graph_ok(monkeypatch)
    past = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=5)).isoformat()
    monkeypatch.setattr(settings, "graph_client_secret_expires_on", past)
    result = monitor._check_graph_sync()
    assert result.status == HealthStatus.DEGRADED
    assert "passed 5 day" in result.detail


def test_graph_expiry_ignored_when_not_configured(monkeypatch):
    from app.core.config import settings
    _graph_ok(monkeypatch)
    monkeypatch.setattr(settings, "graph_client_secret_expires_on", "")
    result = monitor._check_graph_sync()
    assert result.status == HealthStatus.OK


def test_graph_expiry_never_downgrades_an_actual_failure(monkeypatch):
    """An expiry date within the warning window must never mask (or
    upgrade the severity report of) an ALREADY-broken live check — DOWN
    stays DOWN, the real error is what gets reported."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "graph_tenant_id", "tid")
    monkeypatch.setattr(settings, "graph_client_id", "cid")
    monkeypatch.setattr(settings, "graph_client_secret", "bad")
    monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResponse(401, {"error_description": "bad secret"}))
    soon = (dt.datetime.now(dt.timezone.utc).date() + dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(settings, "graph_client_secret_expires_on", soon)
    result = monitor._check_graph_sync()
    assert result.status == HealthStatus.DOWN
    assert "bad secret" in result.detail


# --------------------------------------------------------------------- #
# _should_alert — the state-change rule
# --------------------------------------------------------------------- #
def _row(status, last_alert_sent_at=None):
    return SystemHealthCheck(component="x", status=status, last_alert_sent_at=last_alert_sent_at)


def test_should_alert_transitions():
    now = dt.datetime.now(dt.timezone.utc)
    assert monitor._should_alert(_row(HealthStatus.OK), HealthStatus.OK, now) is False
    assert monitor._should_alert(_row(HealthStatus.OK), HealthStatus.DOWN, now) is True       # just broke
    assert monitor._should_alert(_row(HealthStatus.DOWN), HealthStatus.OK, now) is True       # recovered
    assert monitor._should_alert(_row(HealthStatus.DOWN, now), HealthStatus.DOWN, now) is False  # just alerted


def test_should_alert_resends_after_cooldown(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "system_health_alert_resend_hours", 24)
    now = dt.datetime.now(dt.timezone.utc)
    long_ago = now - dt.timedelta(hours=25)
    assert monitor._should_alert(_row(HealthStatus.DOWN, long_ago), HealthStatus.DOWN, now) is True
    recent = now - dt.timedelta(hours=1)
    assert monitor._should_alert(_row(HealthStatus.DOWN, recent), HealthStatus.DOWN, now) is False


# --------------------------------------------------------------------- #
# run_health_check — end to end (DB + alert dispatch), both components
# skipped/not-applicable in the standard test env (no key, no Graph creds)
# unless monkeypatched — confirms it degrades cleanly with nothing configured.
# --------------------------------------------------------------------- #
async def test_run_health_check_is_a_clean_no_op_with_nothing_configured():
    from app.core.database import SessionLocal
    async with SessionLocal() as db:
        result = await monitor.run_health_check(db)
    assert result[HealthComponent.LLM]["applicable"] is False
    assert result[HealthComponent.GRAPH]["applicable"] is False


async def test_run_health_check_sends_exactly_one_alert_on_first_failure_then_none_on_repeat(monkeypatch):
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.services.system_health import alert_mailer

    monkeypatch.setattr(settings, "graph_tenant_id", "tid")
    monkeypatch.setattr(settings, "graph_client_id", "cid")
    monkeypatch.setattr(settings, "graph_client_secret", "bad")
    monkeypatch.setattr(settings, "openai_api_key", "")  # LLM stays not-applicable, isolate to graph
    monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResponse(401, {"error_description": "bad secret"}))

    sent: list[str] = []
    monkeypatch.setattr(alert_mailer, "send_health_alert_email", lambda subject, body: sent.append(subject))

    async with SessionLocal() as db:
        from sqlalchemy import delete
        await db.execute(delete(SystemHealthCheck).where(SystemHealthCheck.component == HealthComponent.GRAPH))
        await db.commit()

        r1 = await monitor.run_health_check(db)
        assert r1[HealthComponent.GRAPH]["status"] == HealthStatus.DOWN
        assert r1[HealthComponent.GRAPH]["alert_sent"] is True
        assert len(sent) == 1

        r2 = await monitor.run_health_check(db)
        assert r2[HealthComponent.GRAPH]["alert_sent"] is False
        assert len(sent) == 1, "must not re-alert on an unchanged, still-broken status within the cooldown"

        # Recovery -> sends a second (resolved) alert.
        monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResponse(200, {"access_token": "ok"}))
        r3 = await monitor.run_health_check(db)
        assert r3[HealthComponent.GRAPH]["status"] == HealthStatus.OK
        assert r3[HealthComponent.GRAPH]["alert_sent"] is True
        assert len(sent) == 2

        await db.execute(delete(SystemHealthCheck).where(SystemHealthCheck.component == HealthComponent.GRAPH))
        await db.commit()


async def test_run_health_check_never_raises_even_if_the_alert_email_itself_fails(monkeypatch):
    """A failed alert SEND must never crash the health check task — that
    would be its own silent failure mode, exactly what this feature exists
    to prevent."""
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.services.system_health import alert_mailer

    monkeypatch.setattr(settings, "graph_tenant_id", "tid")
    monkeypatch.setattr(settings, "graph_client_id", "cid")
    monkeypatch.setattr(settings, "graph_client_secret", "bad")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr("httpx.post", lambda *a, **k: _FakeResponse(401, {"error_description": "bad secret"}))

    def _raise(*a, **k):
        raise RuntimeError("no recipient configured")
    monkeypatch.setattr(alert_mailer, "send_health_alert_email", _raise)

    async with SessionLocal() as db:
        from sqlalchemy import delete
        await db.execute(delete(SystemHealthCheck).where(SystemHealthCheck.component == HealthComponent.GRAPH))
        await db.commit()

        result = await monitor.run_health_check(db)  # must not raise
        assert result[HealthComponent.GRAPH]["alert_sent"] is False
        assert result[HealthComponent.GRAPH]["alert_error"]
        assert result[HealthComponent.GRAPH]["status"] == HealthStatus.DOWN  # row still updated correctly

        await db.execute(delete(SystemHealthCheck).where(SystemHealthCheck.component == HealthComponent.GRAPH))
        await db.commit()


# --------------------------------------------------------------------- #
# Isolation from reminders/OTP — flipping either has zero effect here
# --------------------------------------------------------------------- #
async def test_reminder_and_otp_switches_do_not_affect_system_health(monkeypatch):
    from app.core.config import settings
    from app.core.database import SessionLocal

    monkeypatch.setattr(settings, "reminder_sending_enabled", False)
    monkeypatch.setattr(settings, "otp_sending_enabled", False)
    monkeypatch.setattr(settings, "reminder_scheduled_check_enabled", False)
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "graph_tenant_id", "")

    async with SessionLocal() as db:
        result = await monitor.run_health_check(db)
    assert result[HealthComponent.LLM]["applicable"] is False
    assert result[HealthComponent.GRAPH]["applicable"] is False


def test_system_health_switches_do_not_affect_reminders_config_defaults():
    """The inverse direction — this feature's own settings existing at all
    must not shift reminders'/OTP's own values (both explicitly set true for
    the test suite in conftest.py, independent of this feature entirely)."""
    from app.core.config import settings
    assert settings.reminder_sending_enabled is True
    assert settings.otp_sending_enabled is True


# --------------------------------------------------------------------- #
# HTTP routes — admin-only, matching reminders.router's own RBAC
# --------------------------------------------------------------------- #
async def test_system_health_routes_are_admin_only(client, admin_token):
    from tests.conftest import login_2fa

    h = auth_headers(admin_token)
    u = await client.post("/api/v1/admin/users", headers=h, json={
        "username": "health-check-user", "password": "pw12345678", "role": "user", "auth_mode": "captcha",
    })
    assert u.status_code == 201, u.text
    user_token = await login_2fa(client, "health-check-user", "pw12345678")
    uh = auth_headers(user_token)

    r = await client.get("/api/v1/system-health", headers=uh)
    assert r.status_code == 403

    r2 = await client.get("/api/v1/system-health", headers=h)
    assert r2.status_code == 200
    assert isinstance(r2.json(), list)


async def test_check_now_route_returns_a_result_per_component(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/system-health/check-now", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {HealthComponent.LLM, HealthComponent.GRAPH}
