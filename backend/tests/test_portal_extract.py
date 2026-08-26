"""Portal extraction: no pass 1 ever runs, one staged PipelineFile per
present slot, and the vault-bound raw copy is the employee's exact original
bytes — never the synthetic .eml wrapper used only to feed pass 2."""
from sqlalchemy import select

from tests.conftest import auth_headers

from app.core.database import SessionLocal
from app.models.pipeline_file import PipelineFile
from app.services.pipeline import raw_store


def _pdf(name="Portal Person", emp_id="PORTAL-X1", month="July 2026", rows=(("2026-07-06", "Annual Leave"),)):
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "", 12)
    for ln in [f"Employee Name: {name}", f"Employee ID: {emp_id}", f"Month: {month}"]:
        pdf.cell(0, 8, ln, new_x="LMARGIN", new_y="NEXT")
    for d, s in rows:
        pdf.cell(0, 8, f"{d} {s}", new_x="LMARGIN", new_y="NEXT")
    out = pdf.output()
    return bytes(out) if isinstance(out, (bytes, bytearray)) else out.encode("latin-1")


async def _employee(client, h, employee_id, name):
    r = await client.post("/api/v1/employee-matcher", headers=h,
                          json={"employee_id": employee_id, "name": name, "location": "DXB",
                                "account_manager": "Portal Extract Manager"})
    assert r.status_code == 201, r.text
    return r.json()


async def _portal_employee(client, h, username, employee_pk):
    r = await client.post("/api/v1/admin/portal-users", headers=h, json={
        "username": username, "password": "supersecret1", "employee_pk": employee_pk})
    assert r.status_code == 201, r.text
    login = await client.post("/api/v1/portal/auth/login", json={"username": username, "password": "supersecret1"})
    return auth_headers(login.json()["access_token"])


async def test_portal_extraction_never_calls_pass1(client, admin_token, mock_vision_calls, monkeypatch):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "PORTAL-X1", "Portal Person")
    ph = await _portal_employee(client, h, "portal.extract1", emp["id"])

    def _boom(*a, **kw):
        raise AssertionError("pass 1 (triage_prompt) must never run for a portal submission")
    monkeypatch.setattr("app.services.extract_email.triage_prompt.run_pass1_batch", _boom)

    created = await client.post("/api/v1/portal/employee/submissions", headers=ph, json={"month": 7, "year": 2026})
    sub_id = created.json()["id"]
    data = _pdf()
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=ph,
                      params={"kind": "timesheet"},
                      files={"file": ("portal_timesheet.pdf", data, "application/pdf")})

    mock_vision_calls([{"sheets": [{
        "source": "A1", "employee_name": "Portal Person", "employee_id": "PORTAL-X1",
        "month": 7, "year": 2026, "days_covered": 1, "period_type": "partial",
        "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
        "annual": ["2026-07-06"], "remote": [], "sick": [], "maternity": [],
        "unpaid": [], "absent": [], "public_holiday": [], "other": [], "notes": "",
    }]}])
    r = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=ph,
                          json={"approval_claimed": True})
    assert r.status_code == 200, r.text
    assert r.json()["extraction_state"] == "done"


async def test_one_staged_pipeline_file_per_slot_with_original_bytes(client, admin_token, mock_vision_calls):
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "PORTAL-X2", "Portal Person Two")
    ph = await _portal_employee(client, h, "portal.extract2", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=ph, json={"month": 8, "year": 2026})
    sub_id = created.json()["id"]

    timesheet_bytes = _pdf(name="Portal Person Two", emp_id="PORTAL-X2", month="August 2026")
    sick_bytes = b"%PDF-1.4 a totally different fake sick note body, not a real pdf but distinct bytes"
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=ph,
                      params={"kind": "timesheet"},
                      files={"file": ("portal_timesheet2.pdf", timesheet_bytes, "application/pdf")})
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=ph,
                      params={"kind": "sick_leave"},
                      files={"file": ("portal_sick2.pdf", sick_bytes, "application/pdf")})

    # Two files present -> ONE pass-2 call covering both (small/no images, one
    # batch) -> two distinct "source" sheets so each resolves back to its own file.
    mock_vision_calls([{"sheets": [
        {"source": "A1", "employee_name": "Portal Person Two", "employee_id": "PORTAL-X2",
         "month": 8, "year": 2026, "days_covered": 1, "period_type": "partial",
         "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
         "annual": ["2026-08-03"], "remote": [], "sick": [], "maternity": [],
         "unpaid": [], "absent": [], "public_holiday": [], "other": [], "notes": ""},
        {"source": "A2", "employee_name": "Portal Person Two", "employee_id": "PORTAL-X2",
         "month": 8, "year": 2026, "days_covered": 0, "period_type": "partial",
         "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
         "annual": [], "remote": [], "sick": ["2026-08-10"], "maternity": [],
         "unpaid": [], "absent": [], "public_holiday": [], "other": [], "notes": ""},
    ]}])
    r = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=ph,
                          json={"approval_claimed": False})
    assert r.status_code == 200, r.text
    assert r.json()["extraction_state"] == "done"

    async with SessionLocal() as db:
        rows = (await db.execute(select(PipelineFile).where(
            PipelineFile.source_kind == "portal",
            PipelineFile.source_id.like(f"portal:{sub_id}:%"),
        ))).scalars().all()
        assert len(rows) == 2, "one staged PipelineFile per present slot, not one shared row"
        by_kind = {r.source_id.rsplit(":", 1)[-1]: r for r in rows}
        assert set(by_kind) == {"timesheet", "sick_leave"}
        assert by_kind["timesheet"].employee_id == "PORTAL-X2"

        # The vault-bound raw copy must be the EXACT original bytes/filename —
        # never the synthetic .eml wrapper used only to feed the pass-2 call.
        ts_row = by_kind["timesheet"]
        assert ts_row.filename == "portal_timesheet2.pdf"
        stored = raw_store.read_raw(ts_row.raw_path)
        assert stored == timesheet_bytes

        sick_row = by_kind["sick_leave"]
        assert sick_row.filename == "portal_sick2.pdf"
        assert raw_store.read_raw(sick_row.raw_path) == sick_bytes


async def test_adding_a_second_file_does_not_reprocess_the_first(client, admin_token, mock_vision_calls):
    """Uploading a new slot onto an already-submitted submission must
    extract ONLY that new slot — the already-staged, unrelated file must be
    left exactly as it was (same PipelineFile row, same raw bytes), not
    silently re-run through the LLM again. Only one vision reply is queued
    below for the second upload; if the fix regressed and the first file got
    reprocessed too, the mock would raise "exhausted" instead of the test
    passing quietly with a false green."""
    h = auth_headers(admin_token)
    emp = await _employee(client, h, "PORTAL-X3", "Portal Person Three")
    ph = await _portal_employee(client, h, "portal.extract3", emp["id"])

    created = await client.post("/api/v1/portal/employee/submissions", headers=ph, json={"month": 9, "year": 2026})
    sub_id = created.json()["id"]
    timesheet_bytes = _pdf(name="Portal Person Three", emp_id="PORTAL-X3", month="September 2026")
    await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=ph,
                      params={"kind": "timesheet"},
                      files={"file": ("portal_timesheet3.pdf", timesheet_bytes, "application/pdf")})

    mock_vision_calls([{"sheets": [{
        "source": "A1", "employee_name": "Portal Person Three", "employee_id": "PORTAL-X3",
        "month": 9, "year": 2026, "days_covered": 1, "period_type": "partial",
        "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
        "annual": ["2026-09-06"], "remote": [], "sick": [], "maternity": [],
        "unpaid": [], "absent": [], "public_holiday": [], "other": [], "notes": "",
    }]}])
    r = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/submit", headers=ph,
                          json={"approval_claimed": True})
    assert r.status_code == 200, r.text

    async with SessionLocal() as db:
        ts_before = (await db.execute(select(PipelineFile).where(
            PipelineFile.source_kind == "portal",
            PipelineFile.source_id == f"portal:{sub_id}:timesheet",
        ))).scalar_one()
        ts_id_before, ts_raw_path_before = ts_before.id, ts_before.raw_path

    # Only ONE reply queued — covers just the sick_leave file. If the fix
    # regressed and the timesheet got reprocessed too, this batch would need
    # a second reply and the mock would raise instead of the request
    # succeeding.
    mock_vision_calls([{"sheets": [{
        "source": "A1", "employee_name": "Portal Person Three", "employee_id": "PORTAL-X3",
        "month": 9, "year": 2026, "days_covered": 1, "period_type": "partial",
        "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
        "annual": [], "remote": [], "sick": ["2026-09-10"], "maternity": [],
        "unpaid": [], "absent": [], "public_holiday": [], "other": [], "notes": "",
    }]}])
    added = await client.post(f"/api/v1/portal/employee/submissions/{sub_id}/files", headers=ph,
                              params={"kind": "sick_leave"},
                              files={"file": ("portal_sick3.pdf", b"%PDF-1.4 sick3", "application/pdf")})
    assert added.status_code == 200, added.text
    assert added.json()["extraction_state"] == "done"

    async with SessionLocal() as db:
        ts_after = (await db.execute(select(PipelineFile).where(
            PipelineFile.source_kind == "portal",
            PipelineFile.source_id == f"portal:{sub_id}:timesheet",
        ))).scalar_one()
        assert ts_after.id == ts_id_before, "the untouched timesheet's staged row must not be recreated"
        assert ts_after.raw_path == ts_raw_path_before, "its stored raw copy must not be rewritten either"

        sick_row = (await db.execute(select(PipelineFile).where(
            PipelineFile.source_kind == "portal",
            PipelineFile.source_id == f"portal:{sub_id}:sick_leave",
        ))).scalar_one()
        assert sick_row.filename == "portal_sick3.pdf"
