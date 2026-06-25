"""End-to-end auth: admin 2FA, OTP lifecycle, RBAC (incl. viewer), rate limits."""
import pytest

from tests.conftest import _login, auth_headers, login_2fa


async def _make_user(client, admin_token, username="alice", mode="otp", email="alice@example.com", pw="Password123"):
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": username, "password": pw, "email": email,
                                "role": "user", "auth_mode": mode})
    assert r.status_code == 201, r.text
    return r.json()


async def test_admin_login_captcha_mode(client):
    # Bootstrap admin uses legacy captcha auth_mode: inline CAPTCHA completes login.
    r = await _login(client, "admin", "admin")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "authenticated"
    assert body["access_token"]
    assert body["user"]["role"] == "admin"
    token = await login_2fa(client, "admin", "admin")
    me = await client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.status_code == 200 and me.json()["role"] == "admin"


async def test_wrong_password_rejected(client):
    from tests.conftest import _fetch_captcha
    cid, answer = await _fetch_captcha(client)
    r = await client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "nope", "captcha_id": cid, "captcha_answer": answer})
    assert r.status_code == 401


async def test_otp_full_lifecycle(client, admin_token):
    await _make_user(client, admin_token, username="otpuser", mode="otp")
    # step 1: password -> otp_required + debug code (dev only)
    r = await _login(client, "otpuser", "Password123")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "otp_required"
    assert data["login_token"]
    code = data["debug_otp"]
    assert code and len(code) == 6

    # wrong code -> 401 with attempts remaining
    bad = await client.post("/api/v1/auth/verify-otp",
                            json={"login_token": data["login_token"], "code": "000000"})
    assert bad.status_code == 401

    # correct code -> access token
    ok = await client.post("/api/v1/auth/verify-otp",
                           json={"login_token": data["login_token"], "code": code})
    assert ok.status_code == 200, ok.text
    token = ok.json()["access_token"]

    # the access token works on a protected route
    me = await client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["username"] == "otpuser"


async def test_otp_single_use(client, admin_token):
    await _make_user(client, admin_token, username="otponce", email="o@e.com")
    r = (await _login(client, "otponce", "Password123")).json()
    code = r["debug_otp"]
    first = await client.post("/api/v1/auth/verify-otp", json={"login_token": r["login_token"], "code": code})
    assert first.status_code == 200
    # reusing the same code/flow fails (consumed)
    second = await client.post("/api/v1/auth/verify-otp", json={"login_token": r["login_token"], "code": code})
    assert second.status_code == 401


async def test_otp_resend_gives_new_code(client, admin_token):
    await _make_user(client, admin_token, username="resend", email="r@e.com")
    r = (await _login(client, "resend", "Password123")).json()
    old = r["debug_otp"]
    rs = await client.post("/api/v1/auth/resend-otp", json={"login_token": r["login_token"]})
    assert rs.status_code == 200, rs.text
    new = rs.json()["debug_otp"]
    assert new and new != old
    # old code no longer valid, new code works
    assert (await client.post("/api/v1/auth/verify-otp",
            json={"login_token": r["login_token"], "code": old})).status_code == 401
    assert (await client.post("/api/v1/auth/verify-otp",
            json={"login_token": r["login_token"], "code": new})).status_code == 200


async def test_fingerprint_mismatch_blocks_verify(client, admin_token):
    await _make_user(client, admin_token, username="fp", email="fp@e.com")
    r = (await _login(client, "fp", "Password123")).json()
    # verify with a different fingerprint header
    bad = await client.post("/api/v1/auth/verify-otp",
                            headers={"X-Fingerprint": "different"},
                            json={"login_token": r["login_token"], "code": r["debug_otp"]})
    assert bad.status_code == 401


async def test_protected_routes_require_auth(client):
    # no token
    assert (await client.get("/api/v1/pipeline")).status_code == 401
    # admin route forbidden for non-admin (build a normal user token)
    admin_token = await login_2fa(client, "admin", "admin")
    await _make_user(client, admin_token, username="plainuser", email="p@e.com")
    login = (await _login(client, "plainuser", "Password123")).json()
    verify = await client.post("/api/v1/auth/verify-otp",
                               json={"login_token": login["login_token"], "code": login["debug_otp"]})
    user_token = verify.json()["access_token"]
    forbidden = await client.get("/api/v1/admin/users", headers=auth_headers(user_token))
    assert forbidden.status_code == 403


async def test_viewer_role_is_read_only(client, admin_token):
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": "vonly", "password": "Password123", "email": "vo@e.com",
                                "role": "viewer", "auth_mode": "otp"})
    assert r.status_code == 201, r.text
    token = await login_2fa(client, "vonly", "Password123")
    # viewer CAN read
    assert (await client.get("/api/v1/pipeline", headers=auth_headers(token))).status_code == 200
    # viewer CANNOT write — blocked at the router (403) before reaching the handler
    w = await client.delete("/api/v1/pipeline/nope", headers=auth_headers(token))
    assert w.status_code == 403, w.text
    # and cannot reach admin routes at all
    assert (await client.get("/api/v1/admin/users", headers=auth_headers(token))).status_code == 403


async def test_password_policy_enforced(client, admin_token):
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": "shortpw", "password": "123", "email": "s@e.com",
                                "role": "user", "auth_mode": "otp"})
    assert r.status_code == 400, r.text


async def test_otp_mode_requires_email(client, admin_token):
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token),
                          json={"username": "noemail", "password": "Password123",
                                "role": "user", "auth_mode": "otp"})
    assert r.status_code == 400, r.text


async def test_logout_revokes_token(client, admin_token):
    assert (await client.get("/api/v1/auth/me", headers=auth_headers(admin_token))).status_code == 200
    out = await client.post("/api/v1/auth/logout", headers=auth_headers(admin_token))
    assert out.status_code == 200
    # the revoked token no longer works
    assert (await client.get("/api/v1/auth/me", headers=auth_headers(admin_token))).status_code == 401


async def test_login_rate_limit(client, admin_token):
    from app.core.config import settings
    from tests.conftest import _fetch_captcha
    await _make_user(client, admin_token, username="brute", email="b@e.com")
    original = settings.login_rate_max
    settings.login_rate_max = 5
    try:
        statuses = []
        for _ in range(9):
            cid, answer = await _fetch_captcha(client)
            rr = await client.post("/api/v1/auth/login", json={
                "username": "brute", "password": "wrong",
                "captcha_id": cid, "captcha_answer": answer,
            })
            statuses.append(rr.status_code)
        assert 429 in statuses, statuses
    finally:
        settings.login_rate_max = original


async def test_totp_login_flow(client, admin_token):
    import pyotp

    created = await _make_user(client, admin_token, username="totpuser", mode="totp", email="t@e.com")
    setup = await client.post(f"/api/v1/admin/users/{created['id']}/totp-setup",
                              headers=auth_headers(admin_token))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["manual_secret"]

    r = await _login(client, "totpuser", "Password123")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] in ("totp_required", "totp_enrollment_required")
    code = pyotp.TOTP(secret).now()
    bad = await client.post("/api/v1/auth/verify-totp",
                            json={"login_token": data["login_token"], "code": "000000"})
    assert bad.status_code == 401
    ok = await client.post("/api/v1/auth/verify-totp",
                           json={"login_token": data["login_token"], "code": code})
    assert ok.status_code == 200, ok.text
    token = ok.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.json()["username"] == "totpuser"
