"""CAPTCHA on login, admin user management, and read-only AI config status."""
from tests.conftest import _login, auth_headers


async def test_captcha_mode_login_requires_captcha_challenge(client, admin_token):
    from tests.conftest import _fetch_captcha

    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": "capuser", "password": "Password123",
                                "email": "c@e.com", "role": "user", "auth_mode": "captcha"})
    assert r.status_code == 201, r.text

    login = await _login(client, "capuser", "Password123")
    assert login.status_code == 200
    data = login.json()
    assert data["status"] == "captcha_required"
    assert data["login_token"] and not data.get("access_token")

    cid, answer = await _fetch_captcha(client)
    done = await client.post("/api/v1/auth/verify-captcha", json={
        "login_token": data["login_token"], "captcha_id": cid, "answer": answer})
    assert done.status_code == 200, done.text
    assert done.json()["access_token"]


async def test_admin_create_assign_email_and_switch_mode(client, admin_token):
    created = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                                json={"username": "switch", "password": "Password123",
                                      "email": "s@e.com", "role": "user", "auth_mode": "otp"})
    uid = created.json()["id"]
    sw = await client.post(f"/api/v1/admin/users/{uid}/auth-mode?mode=totp",
                           headers=auth_headers(admin_token))
    assert sw.status_code == 200
    assert sw.json()["auth_mode"] == "totp"
    up = await client.patch(f"/api/v1/admin/users/{uid}", headers=auth_headers(admin_token),
                            json={"email": "new@e.com"})
    assert up.json()["email"] == "new@e.com"


async def test_otp_user_requires_email(client, admin_token):
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": "noemail", "password": "Password123",
                                "role": "user", "auth_mode": "otp"})
    assert r.status_code == 400


async def test_config_write_endpoints_removed(client, admin_token):
    """AI config is .env-only — admin API is read-only."""
    h = auth_headers(admin_token)
    assert (await client.get("/api/v1/admin/config", headers=h)).status_code == 404
    assert (await client.put("/api/v1/admin/config", headers=h,
                             json={"values": {"ai_provider": "openai"}})).status_code == 404
    assert (await client.get("/api/v1/admin/config/prompts/all", headers=h)).status_code == 404


async def test_config_status_read_only(client, admin_token):
    """provider/model are RESOLVED from settings, not hardcoded — this must
    reflect whatever settings.llm_provider actually is (e.g. "openrouter"),
    never a stale "openai" literal regardless of what is configured."""
    from app.core.config import settings

    h = auth_headers(admin_token)
    r = await client.get("/api/v1/admin/config/status", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    kinds = {item["kind"] for item in body}
    assert kinds == {"extraction", "agent"}
    expected_provider = (settings.llm_provider or "openai").strip().lower()
    assert all(item["provider"] == expected_provider for item in body)


async def test_config_reveal_endpoint_removed(client, admin_token):
    r = await client.post("/api/v1/admin/config/reveal/openai_api_key",
                          headers=auth_headers(admin_token), json={"password": "admin"})
    assert r.status_code in (404, 405)
