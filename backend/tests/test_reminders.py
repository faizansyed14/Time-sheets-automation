"""Timesheet reminder service — config toggle, missing-employee detection,
send-now + duplicate prevention, test sends, and batch runs with per-employee
success/failure handling.

Graph creds are blank in the test environment (see conftest.py), so
mailer.send_reminder_email takes its dev fallback (log + return, no
exception) for any employee WITH a resolvable email — "sent" in these tests
means "the dev fallback path completed without raising", exactly mirroring
how test_auto_accept_staging.py etc. exercise the OTP dev fallback. The one
reliable way to force a FAILED outcome without monkeypatching the mailer is
an employee with no email on file at all — used throughout below.
"""
from __future__ import annotations

from tests.conftest import auth_headers, login_2fa


async def _make_employee(client, h, *, name: str, manager: str = "Reminder Manager",
                         email: str | None = "person@example.com") -> str:
    body = {"employee_id": f"REM-{name}", "name": name, "location": "DXB", "account_manager": manager}
    if email:
        body["employee_email_id"] = email
    r = await client.post("/api/v1/employee-matcher", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _file_timesheet_record(employee_pk: str, name: str, month: int, year: int):
    from app.core.database import SessionLocal
    from app.models.timesheet_record import TimesheetRecord

    async with SessionLocal() as db:
        rec = TimesheetRecord(matched_employee_pk=employee_pk, employee_id=f"REM-{name}",
                              employee_name=name, month=month, year=year)
        db.add(rec)
        await db.commit()


async def test_config_default_off_and_toggle(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/reminders/config", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["auto_send_enabled"] is False
    assert r.json()["send_day"] == 28
    assert r.json()["send_hour_uae"] == 9

    r = await client.put("/api/v1/reminders/config", headers=h, json={"auto_send_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["auto_send_enabled"] is True
    assert r.json()["updated_by"] == "admin"

    r = await client.get("/api/v1/reminders/config", headers=h)
    assert r.json()["auto_send_enabled"] is True


async def test_employees_endpoint_flags_missing_vs_filed(client, admin_token):
    h = auth_headers(admin_token)
    missing_pk = await _make_employee(client, h, name="Missing Person")
    filed_pk = await _make_employee(client, h, name="Filed Person")
    await _file_timesheet_record(filed_pk, "Filed Person", 5, 2026)

    r = await client.get("/api/v1/reminders/employees", headers=h, params={"month": 5, "year": 2026})
    assert r.status_code == 200, r.text
    rows = {row["employee_pk"]: row for row in r.json()["rows"]}
    assert rows[missing_pk]["missing"] is True
    assert rows[missing_pk]["last_status"] is None
    assert rows[filed_pk]["missing"] is False


async def test_send_now_success_then_duplicate_prevention(client, admin_token):
    h = auth_headers(admin_token)
    pk = await _make_employee(client, h, name="Send Now Person")

    r = await client.post("/api/v1/reminders/send-now", headers=h,
                          json={"employee_pk": pk, "month": 6, "year": 2026})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    assert r.json()["sent_at"] is not None

    # Second attempt without force -> 409, carrying the prior send's timestamp.
    r2 = await client.post("/api/v1/reminders/send-now", headers=h,
                           json={"employee_pk": pk, "month": 6, "year": 2026})
    assert r2.status_code == 409, r2.text
    assert r2.json()["detail"]["sent_at"] is not None

    # Forced resend succeeds and adds a new log row.
    r3 = await client.post("/api/v1/reminders/send-now", headers=h,
                           json={"employee_pk": pk, "month": 6, "year": 2026, "force": True})
    assert r3.status_code == 200, r3.text
    assert r3.json()["status"] == "sent"
    assert r3.json()["id"] != r.json()["id"]


async def test_send_now_without_email_on_file_is_a_recorded_failure(client, admin_token):
    h = auth_headers(admin_token)
    pk = await _make_employee(client, h, name="No Email Person", email=None)

    r = await client.post("/api/v1/reminders/send-now", headers=h,
                          json={"employee_pk": pk, "month": 7, "year": 2026})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"
    assert "email" in r.json()["error"].lower()


async def test_send_now_unknown_employee_404s(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/reminders/send-now", headers=h,
                          json={"employee_pk": "does-not-exist", "month": 6, "year": 2026})
    assert r.status_code == 404


async def test_test_email_endpoint_sends_to_arbitrary_address(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/reminders/test", headers=h, json={
        "email": "tester@example.com", "month": 8, "year": 2026, "employee_name": "Someone",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "sent"
    assert body["trigger"] == "test"
    assert body["recipient_email"] == "tester@example.com"
    assert body["employee_pk"] is None


async def test_test_email_rejects_invalid_address(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/reminders/test", headers=h,
                          json={"email": "not-an-email", "month": 8, "year": 2026})
    assert r.status_code == 400


async def test_run_batch_handles_mixed_success_and_failure(client, admin_token):
    # Every employee created by an EARLIER test in this module is still
    # "active" and still missing for whatever month/year that test used, so
    # a batch run's totals reflect the whole roster, not just this test's
    # rows — assertions below check only the specific employees this test
    # created, never global counts (see test_run_batch_skips_already_sent_...
    # for the same reasoning).
    h = auth_headers(admin_token)
    ok_pk = await _make_employee(client, h, name="Batch Ok Person")
    bad_pk = await _make_employee(client, h, name="Batch Bad Person", email=None)
    filed_pk = await _make_employee(client, h, name="Batch Filed Person")
    await _file_timesheet_record(filed_pk, "Batch Filed Person", 9, 2026)

    r = await client.post("/api/v1/reminders/run-batch", headers=h, json={"month": 9, "year": 2026})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    r2 = await client.get(f"/api/v1/reminders/runs/{run_id}", headers=h)
    assert r2.status_code == 200, r2.text
    run = r2.json()
    assert run["finished_at"] is not None
    assert run["sent_count"] + run["failed_count"] + run["skipped_count"] == run["total"]

    by_pk = {l["employee_pk"]: l for l in run["logs"]}
    assert by_pk[ok_pk]["status"] == "sent"
    assert by_pk[bad_pk]["status"] == "failed"
    assert filed_pk not in by_pk


async def test_run_batch_skips_already_sent_employee_on_a_second_run(client, admin_token):
    h = auth_headers(admin_token)
    pk = await _make_employee(client, h, name="Repeat Batch Person")

    r1 = await client.post("/api/v1/reminders/run-batch", headers=h, json={"month": 10, "year": 2026})
    run1 = (await client.get(f"/api/v1/reminders/runs/{r1.json()['run_id']}", headers=h)).json()
    log1 = next(l for l in run1["logs"] if l["employee_pk"] == pk)
    assert log1["status"] == "sent"

    r2 = await client.post("/api/v1/reminders/run-batch", headers=h, json={"month": 10, "year": 2026})
    run2 = (await client.get(f"/api/v1/reminders/runs/{r2.json()['run_id']}", headers=h)).json()
    log2 = next(l for l in run2["logs"] if l["employee_pk"] == pk)
    assert log2["status"] == "skipped"


async def test_runs_list_returns_most_recent_first(client, admin_token):
    h = auth_headers(admin_token)
    await _make_employee(client, h, name="Listed Run Person")
    r = await client.post("/api/v1/reminders/run-batch", headers=h, json={"month": 11, "year": 2026})
    run_id = r.json()["run_id"]

    r2 = await client.get("/api/v1/reminders/runs", headers=h)
    assert r2.status_code == 200
    assert any(row["id"] == run_id for row in r2.json())


async def test_viewer_can_read_but_not_send(client, admin_token):
    h = auth_headers(admin_token)
    pk = await _make_employee(client, h, name="Viewer Guard Person")

    u = await client.post("/api/v1/admin/users", headers=h, json={
        "username": "reminders-viewer", "password": "pw12345678", "role": "viewer", "auth_mode": "captcha",
    })
    assert u.status_code == 201, u.text
    viewer_token = await login_2fa(client, "reminders-viewer", "pw12345678")
    vh = auth_headers(viewer_token)

    r = await client.get("/api/v1/reminders/config", headers=vh)
    assert r.status_code == 200

    r2 = await client.post("/api/v1/reminders/send-now", headers=vh,
                           json={"employee_pk": pk, "month": 12, "year": 2026})
    assert r2.status_code == 403


async def test_scheduled_check_gated_by_toggle_schedule_and_idempotency(client, admin_token, monkeypatch):
    import datetime as dt

    from app.core.database import SessionLocal
    from app.services.reminders import service

    h = auth_headers(admin_token)
    await _make_employee(client, h, name="Scheduled Check Person")

    on_28th_9am = dt.datetime(2026, 4, 28, 9, 0, tzinfo=service.UAE_TZ)

    async with SessionLocal() as db:
        # Toggle off -> never sends, regardless of date/time. Explicit,
        # since an earlier test in this module may have left the (shared,
        # session-wide) singleton config row turned on.
        await service.set_config(db, enabled=False, updated_by="admin")
        monkeypatch.setattr(service, "uae_now", lambda: on_28th_9am)
        result = await service.run_scheduled_check(db)
        assert result["ran"] is False
        assert "off" in result["reason"]

        await service.set_config(db, enabled=True, updated_by="admin")

        # Wrong time of day -> still no send even with the toggle on.
        monkeypatch.setattr(service, "uae_now", lambda: on_28th_9am.replace(hour=10))
        result = await service.run_scheduled_check(db)
        assert result["ran"] is False
        assert "scheduled time" in result["reason"]

        # Right day + hour + toggle on -> sends.
        monkeypatch.setattr(service, "uae_now", lambda: on_28th_9am)
        result = await service.run_scheduled_check(db)
        assert result["ran"] is True
        assert result["run_id"]

        # A second tick the same UAE day never runs again.
        result2 = await service.run_scheduled_check(db)
        assert result2["ran"] is False
        assert "already ran" in result2["reason"]
