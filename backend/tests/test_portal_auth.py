"""Portal auth: admin-issued employee accounts, JWT namespace isolation
between the internal `access` token and the portal `portal_access` token.

No manager role — approval is the internal timesheet team's job via Compare
& Fix, not a separate manager portal login."""
from tests.conftest import auth_headers


async def _employee(client, h, employee_id="PORTAL-E1", name="Portal Employee"):
    r = await client.post("/api/v1/employee-matcher", headers=h,
                          json={"employee_id": employee_id, "name": name, "location": "DXB"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_admin_creates_employee_account_and_it_logs_in(client, admin_token):
    h = auth_headers(admin_token)
    emp = await _employee(client, h)

    created = await client.post("/api/v1/admin/portal-users", headers=h, json={
        "username": "portal.employee1", "password": "supersecret1", "employee_pk": emp["id"],
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "employee"
    assert body["employee_name"] == "Portal Employee"

    login = await client.post("/api/v1/portal/auth/login", json={
        "username": "portal.employee1", "password": "supersecret1"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    me = await client.get("/api/v1/portal/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["employee_id"] == "PORTAL-E1"


async def test_create_account_requires_a_real_employee(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/admin/portal-users", headers=h, json={
        "username": "portal.nobody", "password": "supersecret1", "employee_pk": "does-not-exist",
    })
    assert r.status_code == 400, r.text


async def test_duplicate_username_rejected(client, admin_token):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, employee_id="PORTAL-E3", name="Portal Employee 3")
    body = {"username": "portal.dup", "password": "supersecret1", "employee_pk": emp["id"]}
    r1 = await client.post("/api/v1/admin/portal-users", headers=h, json=body)
    assert r1.status_code == 201, r1.text
    r2 = await client.post("/api/v1/admin/portal-users", headers=h, json=body)
    assert r2.status_code == 409


async def test_portal_token_rejected_by_internal_routes(client, admin_token):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, employee_id="PORTAL-E4", name="Portal Employee 4")
    await client.post("/api/v1/admin/portal-users", headers=h, json={
        "username": "portal.isolation1", "password": "supersecret1", "employee_pk": emp["id"],
    })
    login = await client.post("/api/v1/portal/auth/login", json={
        "username": "portal.isolation1", "password": "supersecret1"})
    portal_token = login.json()["access_token"]

    # A portal token must NOT work against an internal, `access`-gated route.
    r = await client.get("/api/v1/pipeline", headers={"Authorization": f"Bearer {portal_token}"})
    assert r.status_code == 401


async def test_internal_token_rejected_by_portal_routes(client, admin_token):
    # The internal admin's own `access` token must NOT work against a
    # portal_access-gated route, despite sharing the same signing secret.
    r = await client.get("/api/v1/portal/employee/submissions",
                         headers=auth_headers(admin_token))
    assert r.status_code == 401


async def test_portal_login_rate_limit(client, admin_token):
    """Portal accounts are single-factor (password only, no OTP/TOTP), so
    rate limiting is the only brute-force defense they get — mirrors
    test_auth_otp.py's test_login_rate_limit for the internal /auth/login."""
    from app.core.config import settings

    h = auth_headers(admin_token)
    emp = await _employee(client, h, employee_id="PORTAL-RL1", name="Rate Limit Person")
    await client.post("/api/v1/admin/portal-users", headers=h, json={
        "username": "portal.ratelimited", "password": "supersecret1", "employee_pk": emp["id"],
    })

    original = settings.login_rate_max
    settings.login_rate_max = 5
    try:
        statuses = []
        for _ in range(9):
            rr = await client.post("/api/v1/portal/auth/login", json={
                "username": "portal.ratelimited", "password": "wrong-password"})
            statuses.append(rr.status_code)
        assert 429 in statuses, statuses
    finally:
        settings.login_rate_max = original


async def test_portal_login_rate_limit_is_a_separate_bucket_from_internal_login(client, admin_token):
    """The internal /auth/login and portal /portal/auth/login must not share
    a rate-limit bucket — otherwise a shared office IP could let one
    exhaust the other's attempt budget for an unrelated username."""
    from app.core.config import settings

    h = auth_headers(admin_token)
    emp = await _employee(client, h, employee_id="PORTAL-RL2", name="Rate Limit Person 2")
    await client.post("/api/v1/admin/portal-users", headers=h, json={
        "username": "portal.separatebucket", "password": "supersecret1", "employee_pk": emp["id"],
    })

    original = settings.login_rate_max
    settings.login_rate_max = 5
    try:
        for _ in range(5):
            await client.post("/api/v1/auth/login", json={
                "username": "portal.separatebucket", "password": "wrong",
                "captcha_id": "x", "captcha_answer": "x"})
        # The internal endpoint's own bucket is now exhausted for this
        # username — the portal endpoint must still accept a fresh attempt.
        login = await client.post("/api/v1/portal/auth/login", json={
            "username": "portal.separatebucket", "password": "supersecret1"})
        assert login.status_code == 200, login.text
    finally:
        settings.login_rate_max = original
