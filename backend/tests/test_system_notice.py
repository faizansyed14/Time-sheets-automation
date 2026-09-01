"""System notice — GET reachable by every authenticated role (including
viewer/vault_matcher), PUT admin-only. Singleton row, defaults to
message="" / enabled=False before any admin has ever set it.
"""
from tests.conftest import auth_headers, login_2fa
from httpx import AsyncClient


async def _create_user(client: AsyncClient, admin_token: str, username: str, role: str) -> None:
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token), json={
        "username": username, "password": "Password123",
        "email": f"{username}@example.com", "role": role, "auth_mode": "otp",
    })
    assert r.status_code == 201, r.text


async def test_notice_defaults_to_disabled_with_blank_message(client, admin_token):
    r = await client.get("/api/v1/notice", headers=auth_headers(admin_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"] == ""
    assert body["enabled"] is False


async def test_every_authenticated_role_can_read_the_notice(client, admin_token):
    await _create_user(client, admin_token, "noticeviewer", "viewer")
    await _create_user(client, admin_token, "noticevault", "vault_matcher")

    viewer_token = await login_2fa(client, "noticeviewer", "Password123")
    vault_token = await login_2fa(client, "noticevault", "Password123")

    for token in (viewer_token, vault_token):
        r = await client.get("/api/v1/notice", headers=auth_headers(token))
        assert r.status_code == 200, r.text


async def test_only_admin_can_update_the_notice(client, admin_token):
    await _create_user(client, admin_token, "noticeuser", "user")
    user_token = await login_2fa(client, "noticeuser", "Password123")

    forbidden = await client.put("/api/v1/notice", headers=auth_headers(user_token),
                                 json={"message": "hacked", "enabled": True})
    assert forbidden.status_code == 403

    ok = await client.put("/api/v1/notice", headers=auth_headers(admin_token),
                          json={"message": "System under maintenance tonight.", "enabled": True})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["message"] == "System under maintenance tonight."
    assert body["enabled"] is True
    assert body["updated_by"] == "admin"

    # The update is visible to every role, not just admin.
    seen_by_user = await client.get("/api/v1/notice", headers=auth_headers(user_token))
    assert seen_by_user.json()["message"] == "System under maintenance tonight."
    assert seen_by_user.json()["enabled"] is True


async def test_partial_update_only_touches_the_given_field(client, admin_token):
    await client.put("/api/v1/notice", headers=auth_headers(admin_token),
                     json={"message": "Keep this text", "enabled": True})
    toggled_off = await client.put("/api/v1/notice", headers=auth_headers(admin_token), json={"enabled": False})
    assert toggled_off.status_code == 200
    body = toggled_off.json()
    assert body["message"] == "Keep this text"
    assert body["enabled"] is False
