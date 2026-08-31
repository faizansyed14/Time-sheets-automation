"""GET /files/record-for — the Vault's "View extracted data" action, linking
a vault month folder straight to the /records/{id} page Dashboard/Pipeline/
Chat already use."""
from tests.conftest import auth_headers


async def _make_employee_and_record(client, h, *, name: str, manager: str, month: int, year: int):
    emp = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": f"REC-{name}", "name": name, "location": "DXB", "account_manager": manager,
    })
    assert emp.status_code == 201, emp.text
    employee_pk = emp.json()["id"]

    from app.core.database import SessionLocal
    from app.models.timesheet_record import TimesheetRecord

    async with SessionLocal() as db:
        rec = TimesheetRecord(matched_employee_pk=employee_pk, employee_id=f"REC-{name}",
                              employee_name=name, month=month, year=year)
        db.add(rec)
        await db.commit()
        await db.refresh(rec)
        return employee_pk, rec.id


async def test_record_for_resolves_by_employee_pk(client, admin_token):
    h = auth_headers(admin_token)
    employee_pk, record_id = await _make_employee_and_record(
        client, h, name="Record For Pk", manager="Some Manager", month=6, year=2026)

    r = await client.get("/api/v1/files/record-for", headers=h,
                         params={"month": "June-2026", "employee_pk": employee_pk})
    assert r.status_code == 200, r.text
    assert r.json()["record_id"] == record_id


async def test_record_for_resolves_by_manager_and_folder_name(client, admin_token):
    h = auth_headers(admin_token)
    _pk, record_id = await _make_employee_and_record(
        client, h, name="Record For Folder", manager="Folder Manager", month=7, year=2026)

    r = await client.get("/api/v1/files/record-for", headers=h, params={
        "month": "July-2026", "manager": "Folder Manager", "employee_folder": "Record For Folder",
    })
    assert r.status_code == 200, r.text
    assert r.json()["record_id"] == record_id


async def test_record_for_404s_when_nothing_filed_yet(client, admin_token):
    h = auth_headers(admin_token)
    emp = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "REC-NOFILE", "name": "Record For Nofile", "location": "DXB",
        "account_manager": "Nofile Manager",
    })
    employee_pk = emp.json()["id"]

    r = await client.get("/api/v1/files/record-for", headers=h,
                         params={"month": "August-2026", "employee_pk": employee_pk})
    assert r.status_code == 404


async def test_record_for_404s_when_folder_matches_no_employee(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/files/record-for", headers=h, params={
        "month": "June-2026", "manager": "Nobody's Manager", "employee_folder": "Nobody At All",
    })
    assert r.status_code == 404


async def test_record_for_rejects_unrecognized_month_label(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/files/record-for", headers=h, params={
        "month": "NotAMonth-2026", "employee_pk": "whatever",
    })
    assert r.status_code == 400
