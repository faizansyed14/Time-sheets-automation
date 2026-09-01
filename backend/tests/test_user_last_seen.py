"""Online/offline + last-seen tracking on the Users & Access page.

last_seen_at is bumped (throttled) in api/deps.get_current_user on every
authenticated request; "online" is computed on read (now - last_seen_at <=
ONLINE_THRESHOLD), never stored as its own column — see admin.py's _user_out.
"""
from tests.conftest import auth_headers, login_2fa


async def test_freshly_created_user_is_offline_with_no_last_seen(client, admin_token):
    created = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token), json={
        "username": "neverlogged", "password": "Password123",
        "email": "neverlogged@example.com", "role": "user", "auth_mode": "otp",
    })
    assert created.status_code == 201, created.text

    rows = await client.get("/api/v1/admin/users", headers=auth_headers(admin_token))
    row = next(r for r in rows.json() if r["username"] == "neverlogged")
    assert row["last_seen_at"] is None
    assert row["online"] is False


async def test_logging_in_and_making_a_request_marks_the_user_online(client, admin_token):
    created = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token), json={
        "username": "activeuser", "password": "Password123",
        "email": "activeuser@example.com", "role": "user", "auth_mode": "otp",
    })
    assert created.status_code == 201, created.text

    token = await login_2fa(client, "activeuser", "Password123")
    # Any authenticated request bumps last_seen_at (deps.get_current_user) —
    # /auth/me is the lightest one available.
    me = await client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.status_code == 200

    rows = await client.get("/api/v1/admin/users", headers=auth_headers(admin_token))
    row = next(r for r in rows.json() if r["username"] == "activeuser")
    assert row["last_seen_at"] is not None
    assert row["online"] is True


async def test_admin_itself_shows_online_after_the_login_used_to_get_the_token(client, admin_token):
    rows = await client.get("/api/v1/admin/users", headers=auth_headers(admin_token))
    row = next(r for r in rows.json() if r["username"] == "admin")
    assert row["online"] is True


async def test_logout_clears_last_seen_so_the_dot_flips_offline_immediately(client, admin_token):
    """Revoking the token (the jti denylist) doesn't by itself touch
    last_seen_at — without an explicit clear on logout, this person would
    keep showing as "online" (green dot, "Xm ago") for up to ONLINE_THRESHOLD
    after they'd actually signed out. See auth.py's /logout."""
    created = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token), json={
        "username": "loggedout", "password": "Password123",
        "email": "loggedout@example.com", "role": "user", "auth_mode": "otp",
    })
    assert created.status_code == 201, created.text

    token = await login_2fa(client, "loggedout", "Password123")
    me = await client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.status_code == 200

    before = await client.get("/api/v1/admin/users", headers=auth_headers(admin_token))
    row = next(r for r in before.json() if r["username"] == "loggedout")
    assert row["online"] is True

    out = await client.post("/api/v1/auth/logout", headers=auth_headers(token))
    assert out.status_code == 200, out.text

    after = await client.get("/api/v1/admin/users", headers=auth_headers(admin_token))
    row = next(r for r in after.json() if r["username"] == "loggedout")
    assert row["online"] is False
    assert row["last_seen_at"] is None
