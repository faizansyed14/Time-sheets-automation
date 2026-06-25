"""CAPTCHA login mode, admin user management, and config get/set/test."""
from tests.conftest import auth_headers


async def test_captcha_login_flow(client, admin_token):
    # admin creates a captcha-mode user
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": "capuser", "password": "Password123",
                                "email": "c@e.com", "role": "user", "auth_mode": "captcha"})
    assert r.status_code == 201, r.text

    login = await client.post("/api/v1/auth/login", json={"username": "capuser", "password": "Password123"})
    assert login.status_code == 200
    data = login.json()
    assert data["status"] == "captcha_required"
    assert data["login_token"]
    # the login no longer ships a captcha image/id — the client fetches one
    img = await client.get("/api/v1/auth/captcha")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"
    cid = img.headers["x-captcha-id"]
    from app.core.cache import cache
    answer = await cache.get(f"captcha:{cid}")
    assert answer

    # a wrong answer is rejected
    bad = await client.post("/api/v1/auth/verify-captcha",
                            json={"login_token": data["login_token"],
                                  "captcha_id": cid, "answer": "WRONG-ANSWER"})
    assert bad.status_code == 401

    # a fresh captcha solved with the cached answer succeeds
    img = await client.get("/api/v1/auth/captcha")
    cid = img.headers["x-captcha-id"]
    answer = await cache.get(f"captcha:{cid}")
    assert answer
    ok = await client.post("/api/v1/auth/verify-captcha",
                           json={"login_token": data["login_token"], "captcha_id": cid, "answer": answer})
    assert ok.status_code == 200, ok.text
    assert ok.json()["access_token"]


async def test_admin_create_assign_email_and_switch_mode(client, admin_token):
    created = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                                json={"username": "switch", "password": "Password123",
                                      "email": "s@e.com", "role": "user", "auth_mode": "otp"})
    uid = created.json()["id"]
    # switch to captcha
    sw = await client.post(f"/api/v1/admin/users/{uid}/auth-mode?mode=captcha",
                           headers=auth_headers(admin_token))
    assert sw.status_code == 200
    assert sw.json()["auth_mode"] == "captcha"
    # update email
    up = await client.patch(f"/api/v1/admin/users/{uid}", headers=auth_headers(admin_token),
                            json={"email": "new@e.com"})
    assert up.json()["email"] == "new@e.com"


async def test_otp_user_requires_email(client, admin_token):
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": "noemail", "password": "Password123",
                                "role": "user", "auth_mode": "otp"})
    assert r.status_code == 400


async def test_config_get_set_masks_secrets(client, admin_token):
    # set a secret + a control
    put = await client.put("/api/v1/admin/config", headers=auth_headers(admin_token),
                           json={"values": {"openai_api_key": "sk-secret-123",
                                            "vision_image_detail": "low",
                                            "ai_provider": "deepseek",
                                            "enable_text_validation": False}})
    assert put.status_code == 200, put.text
    cfg = {c["key"]: c for c in put.json()}
    # secret is masked, never echoed
    assert cfg["openai_api_key"]["is_secret"] is True
    assert cfg["openai_api_key"]["value"] != "sk-secret-123"
    assert cfg["vision_image_detail"]["value"] == "low"
    assert cfg["ai_provider"]["value"] == "deepseek"

    # the live settings object reflects the env-backed change
    from app.core.config import settings
    assert settings.vision_image_detail == "low"
    assert settings.enable_text_validation is False


async def test_config_test_endpoint_handles_no_key(client, admin_token):
    # ensure deepseek selected with no key -> graceful failure, not a crash
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
    # reset so other tests/use see default
    await client.put("/api/v1/admin/config", headers=auth_headers(admin_token),
                     json={"values": {"system_prompt": ""}})
    assert parser.get_prompt("system") == parser.SYSTEM_PROMPT
