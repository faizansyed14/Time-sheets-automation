"""CAPTCHA on login, admin user management, and config get/set/test."""
from tests.conftest import _login, auth_headers


async def test_captcha_mode_login_completes_on_first_step(client, admin_token):
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": "capuser", "password": "Password123",
                                "email": "c@e.com", "role": "user", "auth_mode": "captcha"})
    assert r.status_code == 201, r.text

    login = await _login(client, "capuser", "Password123")
    assert login.status_code == 200
    data = login.json()
    assert data["status"] == "authenticated"
    assert data["access_token"]


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


async def test_config_get_set_masks_secrets(client, admin_token):
    put = await client.put("/api/v1/admin/config", headers=auth_headers(admin_token),
                           json={"values": {"openai_api_key": "sk-secret-123",
                                            "vision_image_detail": "low",
                                            "ai_provider": "deepseek",
                                            "enable_text_validation": False}})
    assert put.status_code == 200, put.text
    cfg = {c["key"]: c for c in put.json()}
    assert cfg["openai_api_key"]["is_secret"] is True
    assert cfg["openai_api_key"]["value"] != "sk-secret-123"
    assert cfg["vision_image_detail"]["value"] == "low"
    assert cfg["ai_provider"]["value"] == "deepseek"

    from app.core.config import settings
    assert settings.vision_image_detail == "low"
    assert settings.enable_text_validation is False


async def test_config_test_endpoint_handles_no_key(client, admin_token):
    await client.put("/api/v1/admin/config", headers=auth_headers(admin_token),
                     json={"values": {"ai_provider": "deepseek", "deepseek_api_key": ""}})
    r = await client.post("/api/v1/admin/config/test", headers=auth_headers(admin_token),
                          json={"provider": "deepseek", "prompt": "ping"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "key" in (body["error"] or "").lower()


async def test_prompt_override_applies(client, admin_token):
    await client.put("/api/v1/admin/config", headers=auth_headers(admin_token),
                     json={"values": {"system_prompt": "CUSTOM SYSTEM PROMPT"}})
    from app.services.extraction import parser
    assert parser.get_prompt("system") == "CUSTOM SYSTEM PROMPT"
    await client.put("/api/v1/admin/config", headers=auth_headers(admin_token),
                     json={"values": {"system_prompt": ""}})
    assert parser.get_prompt("system") == parser.SYSTEM_PROMPT
