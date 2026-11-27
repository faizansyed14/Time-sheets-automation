"""Security fixes from the OWASP review against Alpha-CV's controls.

  - files that can carry script (HTML / SVG / XML) are served under a sandbox CSP;
  - wrong passwords are limited per ACCOUNT from any address, correct ones never count;
  - an unknown username costs the same password check as a wrong password;
  - CAPTCHA limits do not block an office sharing one IP;
  - prod refuses to start with sign-in switched off or debug codes exposed.
"""
import pytest

from app.core.cache import cache
from tests.conftest import auth_headers


async def _make_user(client, admin_token, username):
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token), json={
        "username": username, "password": "Password123", "email": f"{username}@example.com",
        "role": "viewer", "auth_mode": "otp"})
    assert r.status_code == 201, r.text


async def test_active_files_get_a_sandbox_csp_and_pdfs_do_not(client, admin_token, tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services import storage_provider as sp

    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    if hasattr(sp, "_provider"):
        monkeypatch.setattr(sp, "_provider", None, raising=False)
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "evil.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    (tmp_path / "x" / "evil.html").write_bytes(b"<script>alert(1)</script>")
    (tmp_path / "x" / "sheet.pdf").write_bytes(b"%PDF-1.4 test")
    h = auth_headers(admin_token)
    for name in ("evil.svg", "evil.html"):
        r = await client.get("/api/v1/files/content", headers=h, params={"rel_path": f"x/{name}"})
        if r.status_code == 404:
            pytest.skip("storage provider is not the local folder in this test run")
        assert r.status_code == 200, r.text
        assert "sandbox" in r.headers.get("content-security-policy", ""), (name, r.headers)
    pdf = await client.get("/api/v1/files/content", headers=h, params={"rel_path": "x/sheet.pdf"})
    assert "sandbox" not in pdf.headers.get("content-security-policy", "")


async def test_wrong_passwords_are_limited_per_account_from_any_ip(client, admin_token, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "login_fail_max", 3)
    await _make_user(client, admin_token, "failtest")
    await cache.delete("rl:login-fail:failtest")
    try:
        for i in range(3):                                   # three different IPs
            r = await client.post("/api/v1/auth/login", headers={"X-Real-IP": f"10.9.0.{i}"},
                                  json={"username": "failtest", "password": "wrong-one"})
            assert r.status_code == 401
        r = await client.post("/api/v1/auth/login", headers={"X-Real-IP": "10.9.0.99"},
                              json={"username": "failtest", "password": "Password123"})
        assert r.status_code == 429, "guessing from many addresses must be stopped"
    finally:
        await cache.delete("rl:login-fail:failtest")


async def test_correct_sign_ins_never_count_towards_the_limit(client, admin_token, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "login_fail_max", 2)
    monkeypatch.setattr(settings, "login_rate_max", 1000)
    await _make_user(client, admin_token, "goodtest")
    await cache.delete("rl:login-fail:goodtest")
    try:
        for _ in range(5):
            r = await client.post("/api/v1/auth/login", json={"username": "goodtest", "password": "Password123"})
            assert r.status_code == 200, r.text
    finally:
        await cache.delete("rl:login-fail:goodtest")


async def test_unknown_username_runs_a_password_check(client, monkeypatch):
    from app.api.routes import auth as auth_route
    calls = []
    real = auth_route.verify_password
    monkeypatch.setattr(auth_route, "verify_password", lambda p, h: calls.append(h) or real(p, h))
    r = await client.post("/api/v1/auth/login", json={"username": "nobody-here", "password": "x"})
    assert r.status_code == 401
    assert calls == [auth_route._DUMMY_HASH]
    await cache.delete("rl:login-fail:nobody-here")


async def test_an_office_behind_one_ip_can_load_many_captchas(client):
    from app.core.config import settings
    assert settings.captcha_rate_max >= 50
    for _ in range(15):                                      # 15 people signing in at once
        r = await client.get("/api/v1/auth/captcha", headers={"X-Real-IP": "10.20.30.40"})
        assert r.status_code == 200, r.text
    await cache.delete("rl:captcha:10.20.30.40")


@pytest.mark.parametrize("field,value", [("auth_enabled", False), ("debug_otp_enabled", True)])
def test_prod_refuses_unsafe_sign_in_settings(monkeypatch, field, value):
    from app import main
    from app.core.config import settings
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "jwt_secret", "x" * 48)
    monkeypatch.setattr(settings, "default_admin_password", "a-strong-unique-password")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "debug_otp_enabled", False)
    main._assert_prod_secrets()                              # safe settings start fine
    monkeypatch.setattr(settings, field, value)
    with pytest.raises(RuntimeError):
        main._assert_prod_secrets()
