"""CAPTCHA on login, admin user management, and config get/set/test."""
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


async def test_config_api_exposes_only_providers_and_prompts(client, admin_token):
    """Keys/URLs/models are .env-only: the API neither returns nor accepts them."""
    h = auth_headers(admin_token)
    got = await client.get("/api/v1/admin/config", headers=h)
    assert got.status_code == 200
    keys = {c["key"] for c in got.json()}
    assert keys == {"vision_provider", "validation_provider", "ai_provider",
                    "extract_email_system_prompt"}
    assert all(c["is_secret"] is False for c in got.json())

    # Provider switch works…
    put = await client.put("/api/v1/admin/config", headers=h,
                           json={"values": {"ai_provider": "deepseek"}})
    assert put.status_code == 200, put.text
    cfg = {c["key"]: c for c in put.json()}
    assert cfg["ai_provider"]["value"] == "deepseek"
    # …restore
    await client.put("/api/v1/admin/config", headers=h,
                     json={"values": {"ai_provider": "openai"}})


async def test_config_rejects_key_material_and_models(client, admin_token):
    h = auth_headers(admin_token)
    for blocked in ("openai_api_key", "vllm_api_key", "vllm_base_url",
                    "extraction_model", "vision_image_detail"):
        r = await client.put("/api/v1/admin/config", headers=h,
                             json={"values": {blocked: "x"}})
        assert r.status_code == 400, f"{blocked} must not be settable via API"


async def test_config_reveal_endpoint_removed(client, admin_token):
    r = await client.post("/api/v1/admin/config/reveal/openai_api_key",
                          headers=auth_headers(admin_token), json={"password": "admin"})
    assert r.status_code in (404, 405)


async def test_config_test_endpoint_handles_no_key(client, admin_token):
    # DeepSeek has no key in the test env — the live-test reports that cleanly.
    r = await client.post("/api/v1/admin/config/test", headers=auth_headers(admin_token),
                          json={"provider": "deepseek", "prompt": "ping"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "key" in (body["error"] or "").lower()


async def test_prompt_override_applies(client, admin_token):
    """The ONE editable prompt (shared extraction system prompt) overrides the
    built-in default live, and clears back to it."""
    from app.services.agents import full_email_extract as fx
    await client.put("/api/v1/admin/config", headers=auth_headers(admin_token),
                     json={"values": {"extract_email_system_prompt": "CUSTOM SYSTEM PROMPT"}})
    assert fx.system_prompt() == "CUSTOM SYSTEM PROMPT"
    await client.put("/api/v1/admin/config", headers=auth_headers(admin_token),
                     json={"values": {"extract_email_system_prompt": ""}})
    assert fx.system_prompt() == fx._SYSTEM_PROMPT
