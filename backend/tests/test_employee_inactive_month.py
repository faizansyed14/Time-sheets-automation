"""Inactive employees are month-wise in the Dashboard and Export: shown for the
the months BEFORE the one they were inactivated in, gone from that month onward.
Newly added employees count from the month they were added, never earlier.
Rule lives in services/employee/activity.py; export = Python form, dashboard =
SQL form — both exercised here, plus the pure helpers.
"""
from datetime import date, datetime, timezone

from app.models.employee import Employee
from app.models.timesheet_record import TimesheetRecord, ValidationStatus
from app.services.employee.activity import UAE_TZ, in_month, month_key, set_active
from tests.conftest import auth_headers


def _now_ym() -> tuple[int, int]:
    n = datetime.now(UAE_TZ)
    return n.year, n.month


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


async def _create(client, h, employee_id: str, name: str, *, long_standing: bool = True) -> str:
    """long_standing: behave like a row that was on the roster before the join month was
    tracked (joining_date NULL) - what the older tests below are about."""
    r = await client.post("/api/v1/employee-matcher", headers=h,
                          json={"employee_id": employee_id, "name": name, "location": "DXB"})
    assert r.status_code == 201, r.text
    pk = r.json()["id"]
    if long_standing:
        from app.core import datacache
        from app.core.database import SessionLocal
        async with SessionLocal() as db:
            emp = await db.get(Employee, pk)
            emp.joining_date = None
            await db.commit()
        await datacache.bust_employees()
    return pk


async def _delete_employee(pk: str) -> None:
    from sqlalchemy import delete
    from app.core import datacache
    from app.core.database import SessionLocal
    async with SessionLocal() as db:
        await db.execute(delete(TimesheetRecord).where(TimesheetRecord.matched_employee_pk == pk))
        obj = await db.get(Employee, pk)
        if obj:
            await db.delete(obj)
        await db.commit()
    await datacache.bust_employees()


async def _in_export(client, h, pk: str, year: int, month: int) -> bool:
    r = await client.get("/api/v1/timesheets/by-period", params={"month": month, "year": year}, headers=h)
    assert r.status_code == 200, r.text
    # by-period carries the employee_id; ids are unique per test below.
    return any(x["employee_id"] == pk for x in r.json())


async def _in_dashboard(client, h, name: str, year: int, month: int) -> bool:
    r = await client.get("/api/v1/employees/coverage", headers=h,
                         params={"year": year, "month": month, "q": name, "limit": 50})
    assert r.status_code == 200, r.text
    return any(row["employee_name"] == name for row in r.json()["rows"])


async def _dash_total(client, h, year: int, month: int) -> int:
    r = await client.get("/api/v1/employees/coverage", headers=h,
                         params={"year": year, "month": month, "limit": 1})
    assert r.status_code == 200, r.text
    return r.json()["total_employees"]


# ---------------------------------------------------------------- pure helpers
def test_set_active_stamps_the_uae_month_and_keeps_the_original_on_repeat():
    emp = Employee(employee_id="X", name="X", active=True)
    # 21:00 UTC on Oct 31 is already Nov 1 in the UAE (UTC+4).
    set_active(emp, False, now=datetime(2026, 10, 31, 21, 0, tzinfo=timezone.utc))
    assert emp.active is False
    assert emp.inactive_ym == 202611

    set_active(emp, False, now=datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc))
    assert emp.inactive_ym == 202611, "re-saving must not push the cut-off later"

    set_active(emp, True)
    assert emp.active is True and emp.inactive_ym is None and emp.inactivated_at is None


def test_in_month_rule():
    active = Employee(id="a", employee_id="A", name="A", active=True)
    left_oct = Employee(id="b", employee_id="B", name="B", active=False, inactive_ym=month_key(2026, 10))
    legacy = Employee(id="c", employee_id="C", name="C", active=False, inactive_ym=None)

    assert in_month(active, 1, 2020, set())
    assert in_month(left_oct, 9, 2026, set()), "before the inactivation month"
    assert not in_month(left_oct, 10, 2026, set()), "the inactivation month itself: already gone"
    assert not in_month(left_oct, 11, 2026, set()), "the month after"
    assert not in_month(left_oct, 1, 2027, set())
    assert in_month(left_oct, 11, 2026, {"b"}), "data that month keeps them visible"
    assert not in_month(legacy, 10, 2026, set()), "unknown month -> hidden unless they have data"
    assert in_month(legacy, 10, 2026, {"c"})

    joined_oct = Employee(id="d", employee_id="D", name="D", active=True, joining_date=date(2026, 10, 12))
    assert not in_month(joined_oct, 9, 2026, set()), "before they joined"
    assert in_month(joined_oct, 10, 2026, set()), "the month they joined"
    assert in_month(joined_oct, 11, 2026, set())
    assert in_month(joined_oct, 9, 2026, {"d"}), "data that month keeps them visible"


# ---------------------------------------------------------------- API behaviour
async def test_inactivated_employee_shows_through_their_month_then_drops_off(client, admin_token):
    h = auth_headers(admin_token)
    eid, name = "INACT-M1", "Inactive Month Case"
    pk = await _create(client, h, eid, name)
    cur = _now_ym()
    prev = _shift(*cur, -1)
    nxt = _shift(*cur, +1)
    try:
        total_next_before = await _dash_total(client, h, *nxt)

        r = await client.patch(f"/api/v1/employee-matcher/{pk}/status", headers=h, json={"active": False})
        assert r.status_code == 200, r.text
        assert r.json()["active"] is False
        assert r.json()["inactive_ym"] == month_key(*cur)

        for ym, expected in ((prev, True), (cur, False), (nxt, False)):
            assert await _in_export(client, h, eid, *ym) is expected, f"export {ym}"
            assert await _in_dashboard(client, h, name, *ym) is expected, f"dashboard {ym}"

        # The dashboard headline count follows the same month-wise rule.
        assert await _dash_total(client, h, *nxt) == total_next_before - 1
        assert await _in_dashboard(client, h, name, *prev) is True      # history untouched

        # ...and the downloaded XLSX agrees with the preview.
        from io import BytesIO
        from openpyxl import load_workbook
        for ym, expected in ((prev, True), (cur, False)):
            x = await client.get("/api/v1/timesheets/export", params={"month": ym[1], "year": ym[0]}, headers=h)
            assert x.status_code == 200, x.text
            ws = load_workbook(BytesIO(x.content), read_only=True).active
            names = {row[1] for row in ws.iter_rows(min_row=2, values_only=True)}
            assert (name in names) is expected, f"xlsx {ym}"
    finally:
        await _delete_employee(pk)


async def test_reactivating_clears_the_month_and_brings_them_back(client, admin_token):
    h = auth_headers(admin_token)
    eid, name = "INACT-M2", "Reactivated Case"
    pk = await _create(client, h, eid, name)
    nxt = _shift(*_now_ym(), +1)
    try:
        await client.patch(f"/api/v1/employee-matcher/{pk}/status", headers=h, json={"active": False})
        assert await _in_export(client, h, eid, *nxt) is False

        r = await client.patch(f"/api/v1/employee-matcher/{pk}/status", headers=h, json={"active": True})
        assert r.status_code == 200, r.text
        assert r.json()["active"] is True
        assert r.json()["inactive_ym"] is None
        assert await _in_export(client, h, eid, *nxt) is True
        assert await _in_dashboard(client, h, name, *nxt) is True
    finally:
        await _delete_employee(pk)


async def test_editing_an_inactive_employee_does_not_move_their_month(client, admin_token):
    from app.core.database import SessionLocal
    h = auth_headers(admin_token)
    eid, name = "INACT-M3", "Edited While Inactive"
    pk = await _create(client, h, eid, name)
    try:
        async with SessionLocal() as db:
            emp = await db.get(Employee, pk)
            emp.active = False
            emp.inactive_ym = 202001
            await db.commit()

        r = await client.put(f"/api/v1/employee-matcher/{pk}", headers=h,
                             json={"employee_id": eid, "name": name, "location": "AUH", "active": False})
        assert r.status_code == 200, r.text
        assert r.json()["location"] == "AUH"
        assert r.json()["inactive_ym"] == 202001, "an ordinary edit must not restamp the month"

        # Flipping active through the edit form is a real change and clears it.
        r2 = await client.put(f"/api/v1/employee-matcher/{pk}", headers=h,
                              json={"employee_id": eid, "name": name, "location": "AUH", "active": True})
        assert r2.json()["active"] is True and r2.json()["inactive_ym"] is None
    finally:
        await _delete_employee(pk)


async def test_legacy_inactive_employee_only_shows_where_they_have_data(client, admin_token):
    """Rows inactivated before the month was recorded (inactive_ym NULL) are
    hidden from every month unless the pipeline holds data for them that month."""
    from app.core import datacache
    from app.core.database import SessionLocal
    h = auth_headers(admin_token)
    eid, name = "INACT-M4", "Legacy Inactive Case"
    pk = await _create(client, h, eid, name)
    cur = _now_ym()
    try:
        async with SessionLocal() as db:
            emp = await db.get(Employee, pk)
            emp.active = False   # inactive_ym stays NULL, like pre-migration rows
            await db.commit()
        await datacache.bust_employees()

        assert await _in_export(client, h, eid, *cur) is False
        assert await _in_dashboard(client, h, name, *cur) is False

        async with SessionLocal() as db:
            db.add(TimesheetRecord(
                matched_employee_pk=pk, employee_id=eid, employee_name=name,
                month=cur[1], year=cur[0], validation_status=ValidationStatus.VERIFIED,
            ))
            await db.commit()
        await datacache.bust_coverage()

        assert await _in_export(client, h, eid, *cur) is True, "a filed record keeps them in that month"
        assert await _in_dashboard(client, h, name, *cur) is True
        # ...but only that month.
        assert await _in_export(client, h, eid, *_shift(*cur, +1)) is False
    finally:
        await _delete_employee(pk)


async def test_headcount_each_month_is_its_own(client, admin_token):
    """Add someone this month: last month's total and missing list do not change, this
    month's go up by one. Remove someone: they leave from this month on only."""
    h = auth_headers(admin_token)
    cur = _now_ym()
    prev = _shift(*cur, -1)
    nxt = _shift(*cur, +1)
    before = {ym: await _dash_total(client, h, *ym) for ym in (prev, cur, nxt)}
    leaver = await _create(client, h, "JOIN-M2", "Leaves This Month")      # on the roster already
    pk = await _create(client, h, "JOIN-M1", "Joined This Month", long_standing=False)
    try:
        assert await _dash_total(client, h, *prev) == before[prev] + 1          # leaver only (long-standing)
        assert await _in_dashboard(client, h, "Joined This Month", *prev) is False
        assert await _in_dashboard(client, h, "Joined This Month", *cur) is True
        assert await _in_export(client, h, "JOIN-M1", *prev) is False
        assert await _in_export(client, h, "JOIN-M1", *cur) is True

        r = await client.patch(f"/api/v1/employee-matcher/{leaver}/status", headers=h, json={"active": False})
        assert r.status_code == 200, r.text
        assert await _dash_total(client, h, *prev) == before[prev] + 1          # still counted before
        assert await _dash_total(client, h, *cur) == before[cur] + 1            # joiner only
        assert await _dash_total(client, h, *nxt) == before[nxt] + 1
    finally:
        await _delete_employee(pk)
        await _delete_employee(leaver)


async def test_dates_can_be_chosen_and_corrected_from_the_form(client, admin_token):
    """Joining / left dates are editable full dates: back-date the joiner, pick the left
    date, and an old client that does not send them leaves the stored dates alone."""
    h = auth_headers(admin_token)
    pk = await _create(client, h, "FORM-M1", "Form Months Case")
    body = {"employee_id": "FORM-M1", "name": "Form Months Case", "location": "DXB", "active": True}
    put = lambda extra: client.put(f"/api/v1/employee-matcher/{pk}", headers=h, json={**body, **extra})
    try:
        r = await put({"joining_date": "2026-10-12"})
        assert r.status_code == 200 and r.json()["joining_date"] == "2026-10-12", r.text

        r = await put({})                                                    # dates not sent
        assert r.json()["joining_date"] == "2026-10-12", "an old client must not wipe the joining date"

        r = await put({"active": False, "joining_date": "2026-10-12", "left_date": "2026-12-20"})
        assert r.status_code == 200, r.text
        assert r.json()["left_date"] == "2026-12-20" and r.json()["inactive_ym"] == 202612

        assert (await put({"active": False, "joining_date": "2026-10-12", "left_date": "2026-10-01"})).status_code == 422

        r = await put({"joining_date": None})
        assert r.json()["joining_date"] is None, "clearing it means 'every month'"

        c = await client.post("/api/v1/employee-matcher", headers=h,
                              json={"employee_id": "FORM-M2", "name": "Form Months Two", "joining_date": None})
        assert c.status_code == 201 and c.json()["joining_date"] is None
        await _delete_employee(c.json()["id"])
    finally:
        await _delete_employee(pk)
