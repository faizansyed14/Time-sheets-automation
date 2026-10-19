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


async def _ack_state(client, admin_token, username: str) -> dict:
    r = await client.get("/api/v1/notice/acks", headers=auth_headers(admin_token))
    assert r.status_code == 200, r.text
    return next(u for u in r.json()["users"] if u["username"] == username)


async def test_ok_is_remembered_per_user_and_visible_to_admin(client, admin_token):
    await _create_user(client, admin_token, "ackuser", "user")
    await _create_user(client, admin_token, "ackviewer", "viewer")
    user_token = await login_2fa(client, "ackuser", "Password123")
    viewer_token = await login_2fa(client, "ackviewer", "Password123")
    admin_h = auth_headers(admin_token)

    await client.put("/api/v1/notice", headers=admin_h,
                     json={"message": "Maintenance at 10pm", "enabled": True})

    # Nobody has clicked OK yet.
    for token in (user_token, viewer_token):
        assert (await client.get("/api/v1/notice", headers=auth_headers(token))).json()["acknowledged"] is False
    assert (await _ack_state(client, admin_token, "ackuser"))["acknowledged"] is False

    # One user clicks OK (any role may) — only they are marked, and it sticks.
    before = (await client.get("/api/v1/notice", headers=admin_h)).json()
    ok = await client.post("/api/v1/notice/ack", headers=auth_headers(user_token),
                           json={"message": "Maintenance at 10pm"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["acknowledged"] is True
    assert (await client.get("/api/v1/notice", headers=auth_headers(user_token))).json()["acknowledged"] is True
    assert (await client.get("/api/v1/notice", headers=auth_headers(viewer_token))).json()["acknowledged"] is False

    mine = await _ack_state(client, admin_token, "ackuser")
    assert mine["acknowledged"] is True and mine["acked_at"]
    assert (await _ack_state(client, admin_token, "ackviewer"))["acknowledged"] is False
    listing = (await client.get("/api/v1/notice/acks", headers=admin_h)).json()
    assert listing["acknowledged_count"] >= 1 and listing["total"] >= 2

    # Clicking again changes nothing (idempotent), and an ack is not an admin edit.
    again = await client.post("/api/v1/notice/ack", headers=auth_headers(user_token))
    assert again.status_code == 200 and again.json()["acknowledged"] is True
    after = (await client.get("/api/v1/notice", headers=admin_h)).json()
    assert after["updated_at"] == before["updated_at"]
    assert after["updated_by"] == "admin"


async def test_a_new_announcement_resets_everyones_ok(client, admin_token):
    await _create_user(client, admin_token, "resetuser", "user")
    token = await login_2fa(client, "resetuser", "Password123")
    admin_h = auth_headers(admin_token)

    await client.put("/api/v1/notice", headers=admin_h, json={"message": "First notice", "enabled": True})
    await client.post("/api/v1/notice/ack", headers=auth_headers(token))
    assert (await client.get("/api/v1/notice", headers=auth_headers(token))).json()["acknowledged"] is True

    # Saving the SAME message/enabled again is not a new announcement.
    await client.put("/api/v1/notice", headers=admin_h, json={"message": "First notice", "enabled": True})
    assert (await client.get("/api/v1/notice", headers=auth_headers(token))).json()["acknowledged"] is True

    # A different message shows again for everyone.
    await client.put("/api/v1/notice", headers=admin_h, json={"message": "Second notice"})
    assert (await client.get("/api/v1/notice", headers=auth_headers(token))).json()["acknowledged"] is False
    assert (await _ack_state(client, admin_token, "resetuser"))["acknowledged"] is False

    # Acknowledge, then switch off and back on: shown to everyone again.
    await client.post("/api/v1/notice/ack", headers=auth_headers(token))
    await client.put("/api/v1/notice", headers=admin_h, json={"enabled": False})
    assert (await client.get("/api/v1/notice", headers=auth_headers(token))).json()["acknowledged"] is True
    await client.put("/api/v1/notice", headers=admin_h, json={"enabled": True})
    assert (await client.get("/api/v1/notice", headers=auth_headers(token))).json()["acknowledged"] is False


async def test_ok_on_a_stale_message_or_a_disabled_notice_is_not_recorded(client, admin_token):
    await _create_user(client, admin_token, "staleuser", "user")
    token = await login_2fa(client, "staleuser", "Password123")
    admin_h = auth_headers(admin_token)

    await client.put("/api/v1/notice", headers=admin_h, json={"message": "Current text", "enabled": True})
    stale = await client.post("/api/v1/notice/ack", headers=auth_headers(token), json={"message": "Old text"})
    assert stale.json()["acknowledged"] is False, "the admin changed it while the popup was open"

    await client.put("/api/v1/notice", headers=admin_h, json={"enabled": False})
    off = await client.post("/api/v1/notice/ack", headers=auth_headers(token))
    assert off.json()["acknowledged"] is False, "nothing is showing, so there is nothing to acknowledge"


async def test_only_admin_can_see_who_acknowledged(client, admin_token):
    await _create_user(client, admin_token, "ackpeek", "user")
    token = await login_2fa(client, "ackpeek", "Password123")
    r = await client.get("/api/v1/notice/acks", headers=auth_headers(token))
    assert r.status_code == 403


async def test_partial_update_only_touches_the_given_field(client, admin_token):
    await client.put("/api/v1/notice", headers=auth_headers(admin_token),
                     json={"message": "Keep this text", "enabled": True})
    toggled_off = await client.put("/api/v1/notice", headers=auth_headers(admin_token), json={"enabled": False})
    assert toggled_off.status_code == 200
    body = toggled_off.json()
    assert body["message"] == "Keep this text"
    assert body["enabled"] is False
