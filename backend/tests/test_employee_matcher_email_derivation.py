"""POST/PUT /employee-matcher — employee_email_id must always be derived
from work_email/personal_email, the same two fields the bulk importer
already reads from its own WORK EMAIL/PERSONAL EMAIL columns (see
test_employee_import.py). Without this, a row created or edited through the
manual Employee Matcher form (which only exposes Work/Personal email, not
employee_email_id directly) could end up with an address nobody downstream
(chat, exports) can actually see.
"""
from tests.conftest import auth_headers


async def test_create_derives_employee_email_id_from_work_email(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "DERIVE-1", "name": "Derive Create Person", "location": "AUH",
        "work_email": "work@x.ae", "personal_email": "personal@x.ae",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["work_email"] == "work@x.ae"
    assert body["personal_email"] == "personal@x.ae"
    assert body["employee_email_id"] == "work@x.ae"          # work wins as primary


async def test_create_with_only_personal_email_still_derives_a_usable_address(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "DERIVE-2", "name": "Derive Personal Only", "location": "AUH",
        "personal_email": "onlypersonal@x.ae",
    })
    assert r.status_code == 201, r.text
    assert r.json()["employee_email_id"] == "onlypersonal@x.ae"


async def test_create_with_neither_email_leaves_employee_email_id_blank(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "DERIVE-3", "name": "Derive No Email", "location": "AUH",
    })
    assert r.status_code == 201, r.text
    assert r.json()["employee_email_id"] is None


async def test_update_recomputes_when_an_email_slot_changes(client, admin_token):
    h = auth_headers(admin_token)
    created = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "DERIVE-4", "name": "Derive Update Person", "location": "AUH",
        "work_email": "old-work@x.ae",
    })
    pk = created.json()["id"]

    r = await client.put(f"/api/v1/employee-matcher/{pk}", headers=h, json={
        "employee_id": "DERIVE-4", "name": "Derive Update Person", "location": "AUH",
        "work_email": "new-work@x.ae", "personal_email": "new-personal@x.ae",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["employee_email_id"] == "new-work@x.ae"
    assert body["personal_email"] == "new-personal@x.ae"


async def test_update_that_never_touches_email_fields_does_not_wipe_existing_data(client, admin_token):
    """Editing an unrelated field (contact number) on a row that only ever
    had a legacy email (no work/personal split — e.g. a DXB import) must not
    erase that email just because the form round-trips blank
    work_email/personal_email values."""
    h = auth_headers(admin_token)
    created = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "DERIVE-5", "name": "Derive Preserve Person", "location": "DXB",
        "employee_email_id": "legacy@x.ae",
    })
    pk = created.json()["id"]
    assert created.json()["employee_email_id"] == "legacy@x.ae"

    r = await client.put(f"/api/v1/employee-matcher/{pk}", headers=h, json={
        "employee_id": "DERIVE-5", "name": "Derive Preserve Person", "location": "DXB",
        "employee_email_id": "legacy@x.ae", "contact_no": "0501234567",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contact_no"] == "0501234567"
    assert body["employee_email_id"] == "legacy@x.ae"
