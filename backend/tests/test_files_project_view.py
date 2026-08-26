"""File Vault — the project-wise, location-wise, and search-based views (all
alternate, read-oriented lenses onto the SAME Manager/Employee/Month files,
grouped by the Employee Matcher's own `project`/`location` fields instead of
the account manager, or found directly by name/ID/project). Every route
resolves back down to the exact (manager, employee_folder, month) triple the
manager-wise view already uses — no parallel storage structure, no
duplicated files."""
from tests.conftest import auth_headers


async def test_project_view_resolves_to_the_same_vault_files(client, admin_token):
    h = auth_headers(admin_token)
    mgr, emp, month = "Acme Corp", "Priya Nair", "June-2026"

    emp_row = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "PROJ-1", "name": emp, "location": "DXB",
        "account_manager": mgr, "project": "Falcon",
    })
    assert emp_row.status_code == 201, emp_row.text
    employee_pk = emp_row.json()["id"]

    # File the same folder chain the Manager view would use directly.
    await client.post("/api/v1/files/managers", headers=h, json={"name": mgr})
    await client.post(f"/api/v1/files/managers/{mgr}/employees", headers=h, json={"name": emp})
    await client.post(f"/api/v1/files/managers/{mgr}/employees/{emp}/months", headers=h,
                      json={"month_label": month})
    up = await client.post(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/files",
        headers=h, files={"files": ("timesheet.pdf", b"%PDF-1.4 hello", "application/pdf")})
    assert up.status_code == 201, up.text

    # The project shows up, with this one employee counted.
    projects = await client.get("/api/v1/files/projects", headers=h)
    assert projects.status_code == 200, projects.text
    falcon = next(p for p in projects.json() if p["name"] == "Falcon")
    assert falcon["employee_count"] == 1

    # Drilling into the project resolves this employee back to their real
    # manager folder and correctly counts the one month they have.
    proj_employees = await client.get("/api/v1/files/projects/Falcon/employees", headers=h)
    assert proj_employees.status_code == 200, proj_employees.text
    row = next(e for e in proj_employees.json() if e["employee_pk"] == employee_pk)
    assert row["account_manager"] == mgr
    assert row["employee_folder"] == emp   # no ACO/DCO set -> bare name
    assert row["month_count"] == 1

    # And the employee-vault routes surface the SAME uploaded file.
    months = await client.get(f"/api/v1/files/employee-vault/{employee_pk}/months", headers=h)
    assert months.status_code == 200, months.text
    assert any(m["name"] == month for m in months.json())

    items = await client.get(
        f"/api/v1/files/employee-vault/{employee_pk}/months/{month}/items", headers=h)
    assert items.status_code == 200, items.text
    names = {i["name"] for i in items.json()}
    assert "timesheet.pdf" in names
    # The rel_path is identical to the Manager view's own listing — a
    # Project-view file IS the Manager-view file, not a copy.
    manager_items = await client.get(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/items", headers=h)
    assert {i["rel_path"] for i in items.json()} == {i["rel_path"] for i in manager_items.json()}


async def test_project_view_uses_the_aco_dco_labeled_folder(client, admin_token):
    """An employee with an ACO/DCO number is filed under
    "<Name> (ACO-x, DCO-y)", not the bare name — employee_vault_location must
    resolve to that exact label, or the project view would 404 on a real
    employee's actual folder."""
    h = auth_headers(admin_token)
    mgr, emp = "Beta LLC", "Omar Youssef"

    emp_row = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "PROJ-2", "name": emp, "location": "AUH",
        "account_manager": mgr, "project": "Osprey",
        "aco_number": "77", "dco_number": "88",
    })
    assert emp_row.status_code == 201, emp_row.text
    employee_pk = emp_row.json()["id"]
    labeled_folder = f"{emp} (ACO-77, DCO-88)"

    await client.post("/api/v1/files/managers", headers=h, json={"name": mgr})
    await client.post(f"/api/v1/files/managers/{mgr}/employees", headers=h,
                      json={"name": labeled_folder})
    await client.post(f"/api/v1/files/managers/{mgr}/employees/{labeled_folder}/months", headers=h,
                      json={"month_label": "July-2026"})

    proj_employees = await client.get("/api/v1/files/projects/Osprey/employees", headers=h)
    row = next(e for e in proj_employees.json() if e["employee_pk"] == employee_pk)
    assert row["employee_folder"] == labeled_folder
    assert row["month_count"] == 1

    months = await client.get(f"/api/v1/files/employee-vault/{employee_pk}/months", headers=h)
    assert any(m["name"] == "July-2026" for m in months.json())


async def test_employee_vault_route_404s_for_an_unknown_employee(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/files/employee-vault/does-not-exist/months", headers=h)
    assert r.status_code == 404


async def test_location_view_resolves_to_the_same_vault_files(client, admin_token):
    h = auth_headers(admin_token)
    mgr, emp, month = "Delta Group", "Hana Suzuki", "August-2026"

    emp_row = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "LOC-1", "name": emp, "location": "AUH",
        "account_manager": mgr, "project": "Kestrel",
    })
    assert emp_row.status_code == 201, emp_row.text
    employee_pk = emp_row.json()["id"]

    await client.post("/api/v1/files/managers", headers=h, json={"name": mgr})
    await client.post(f"/api/v1/files/managers/{mgr}/employees", headers=h, json={"name": emp})
    await client.post(f"/api/v1/files/managers/{mgr}/employees/{emp}/months", headers=h,
                      json={"month_label": month})
    await client.post(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/files",
        headers=h, files={"files": ("sheet.pdf", b"%PDF-1.4 hi", "application/pdf")})

    locations = await client.get("/api/v1/files/locations", headers=h)
    assert locations.status_code == 200, locations.text
    auh = next(l for l in locations.json() if l["name"] == "AUH")
    assert auh["employee_count"] >= 1

    loc_employees = await client.get("/api/v1/files/locations/AUH/employees", headers=h)
    assert loc_employees.status_code == 200, loc_employees.text
    row = next(e for e in loc_employees.json() if e["employee_pk"] == employee_pk)
    assert row["account_manager"] == mgr
    assert row["month_count"] == 1

    items = await client.get(
        f"/api/v1/files/employee-vault/{employee_pk}/months/{month}/items", headers=h)
    assert any(i["name"] == "sheet.pdf" for i in items.json())


async def test_search_finds_employee_by_name_id_or_project(client, admin_token):
    h = auth_headers(admin_token)

    a = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "SRCH-100", "name": "Zara Ibrahim", "location": "DXB",
        "account_manager": "Gamma Co", "project": "Nimbus",
    })
    assert a.status_code == 201, a.text
    b = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "SRCH-200", "name": "Yusuf Khan", "location": "AUH",
        "account_manager": "Gamma Co", "project": "Zenith",
    })
    assert b.status_code == 201, b.text
    zara_pk, yusuf_pk = a.json()["id"], b.json()["id"]

    by_name = await client.get("/api/v1/files/search-employees", headers=h, params={"q": "Zara"})
    assert by_name.status_code == 200, by_name.text
    assert {e["employee_pk"] for e in by_name.json()} == {zara_pk}

    by_id = await client.get("/api/v1/files/search-employees", headers=h, params={"q": "SRCH-200"})
    assert {e["employee_pk"] for e in by_id.json()} == {yusuf_pk}

    # "project" doubles as the "client name" search — a distinct project per
    # employee disambiguates them even though both share an account manager.
    by_project = await client.get("/api/v1/files/search-employees", headers=h, params={"q": "Nimbus"})
    assert {e["employee_pk"] for e in by_project.json()} == {zara_pk}

    r = by_id.json()[0]
    assert r["account_manager"] == "Gamma Co"
    assert r["employee_folder"] == "Yusuf Khan"
    assert "month_count" in r


async def test_search_requires_a_query(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/files/search-employees", headers=h, params={"q": ""})
    assert r.status_code == 422
