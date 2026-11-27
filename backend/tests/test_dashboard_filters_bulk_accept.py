"""Dashboard Project / Account-Manager filters and the PGC / MOD bulk accept."""
from sqlalchemy import delete, select

from app.core import datacache
from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile, PipelineStatus
from app.models.timesheet_record import TimesheetRecord
from tests.conftest import auth_headers

YEAR, MONTH = 2034, 5   # a period no other test touches


async def _emp(client, h, eid, name, project, manager):
    r = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": eid, "name": name, "project": project,
        "account_manager": manager, "location": "DXB"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _cov(client, h, **params):
    r = await client.get("/api/v1/employees/coverage", headers=h,
                         params={"year": YEAR, "month": MONTH, "limit": 500, **params})
    assert r.status_code == 200, r.text
    return r.json()


async def test_dashboard_filters_by_project_and_account_manager(client, admin_token):
    h = auth_headers(admin_token)
    a = await _emp(client, h, "FLT-1", "Filter Alpha", "FltProjX", "Flt Manager One")
    b = await _emp(client, h, "FLT-2", "Filter Beta", "FltProjX", "Flt Manager Two")
    c = await _emp(client, h, "FLT-3", "Filter Gamma", "FltProjY", "Flt Manager One")
    try:
        everyone = await _cov(client, h)
        assert "FltProjX" in everyone["project_options"] and "FltProjY" in everyone["project_options"]
        assert "Flt Manager One" in everyone["account_manager_options"]

        proj = await _cov(client, h, project="fltprojx")           # case-insensitive
        assert {r["employee_pk"] for r in proj["rows"]} == {a, b}
        assert proj["total_employees"] == 2, "headline counts follow the filter too"
        assert proj["missing_this_month"] == 2

        mgr = await _cov(client, h, account_manager="Flt Manager One")
        assert {r["employee_pk"] for r in mgr["rows"]} == {a, c}

        both = await _cov(client, h, project="FltProjX", account_manager="Flt Manager One")
        assert [r["employee_pk"] for r in both["rows"]] == [a]
        assert both["total_employees"] == 1

        none = await _cov(client, h, project="No Such Project")
        assert none["rows"] == [] and none["total_employees"] == 0
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(Employee).where(Employee.id.in_([a, b, c])))
            await db.commit()
        await datacache.bust_employees()


def _staged(pk, name, blockers=()):
    return {
        "staged": {"employee_pk": pk, "matched_name": name, "month": MONTH, "year": YEAR,
                   "buckets": {"annual": ["2034-05-04"]},
                   "working_days": ["2034-05-01", "2034-05-02"], "weekend_days": ["2034-05-06"]},
        "auto_accept": {"accepted": not blockers, "blockers": list(blockers), "reasons": []},
        "full_email_extract": {"approval": {"detected": False, "detail": ""}},
    }


async def test_bulk_accept_files_pgc_and_mod_items_even_with_flags(client, admin_token):
    h = auth_headers(admin_token)
    # The matcher's real project names: three PGC projects, and MOD = Ministry of Defence.
    pgc = await _emp(client, h, "BA-1", "Bulk Pgc Person", "PGC- TECHNICAL SUPPORT", "Mgr")
    mod = await _emp(client, h, "BA-2", "Bulk Mod Person", " Ministry of Defence ", "Mgr")   # case/space tolerant
    held = await _emp(client, h, "BA-3", "Bulk Held Person", "PGC-MULTIMEDIA", "Mgr")
    other = await _emp(client, h, "BA-4", "Bulk Other Person", "ACME", "Mgr")
    ids: dict[str, str] = {}
    try:
        async with SessionLocal() as db:
            for key, pk, name, blockers in (
                ("pgc", pgc, "Bulk Pgc Person", ["no manager approval evidence found — none"]),
                ("mod", mod, "Bulk Mod Person", []),
                ("held", held, "Bulk Held Person", ["incomplete coverage: 20/31 days"]),
                ("other", other, "Bulk Other Person", []),
            ):
                t = PipelineFile(
                    filename=f"{name}.pdf", source_kind="bulk", source_id="BA-src", thread_key="BA-src",
                    attachment_id=f"ba:{key}", status=PipelineStatus.NEEDS_REVIEW,
                    employee_id=key, employee_name=name, month=MONTH, year=YEAR,
                    extraction_meta=_staged(pk, name, blockers))
                db.add(t)
                await db.flush()
                ids[key] = t.id
            await db.commit()

        # Dry run: PGC + MOD are all ready - even the one with an "incomplete coverage" flag.
        dry = (await client.post("/api/v1/pipeline/bulk-accept", headers=h,
                                 json={"dry_run": True, "month": MONTH, "year": YEAR})).json()
        assert dry["ready"] == 3 and dry["held_count"] == 0
        assert dry["by_project"] == {"PGC": 2, "MOD": 1}
        assert dry["accepted"] == 0

        # A project outside the list is refused.
        bad = await client.post("/api/v1/pipeline/bulk-accept", headers=h, json={"project": "ACME"})
        assert bad.status_code == 400

        # Only MOD first.
        one = (await client.post("/api/v1/pipeline/bulk-accept", headers=h,
                                 json={"project": "mod", "month": MONTH, "year": YEAR})).json()
        assert one["accepted"] == 1 and one["failed"] == [] and one["remaining"] == 0

        both = (await client.post("/api/v1/pipeline/bulk-accept", headers=h,
                                  json={"month": MONTH, "year": YEAR})).json()
        assert both["accepted"] == 2 and both["failed"] == []

        async with SessionLocal() as db:
            status = {k: (await db.get(PipelineFile, v)).status for k, v in ids.items()}
            recs = (await db.execute(select(TimesheetRecord).where(
                TimesheetRecord.matched_employee_pk.in_([pgc, mod, held, other]),
                TimesheetRecord.year == YEAR, TimesheetRecord.month == MONTH))).scalars().all()
        assert status["pgc"] == status["mod"] == status["held"] == PipelineStatus.SUCCESS
        assert status["other"] == PipelineStatus.NEEDS_REVIEW          # not a PGC / MOD employee
        assert {r.matched_employee_pk for r in recs} == {pgc, mod, held}
        assert all(r.annual_leave_dates == ["2034-05-04"] for r in recs)

        again = (await client.post("/api/v1/pipeline/bulk-accept", headers=h,
                                   json={"dry_run": True, "month": MONTH, "year": YEAR})).json()
        assert again["ready"] == 0 and again["held_count"] == 0
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(TimesheetRecord).where(
                TimesheetRecord.matched_employee_pk.in_([pgc, mod, held, other])))
            await db.execute(delete(PipelineFile).where(PipelineFile.thread_key == "BA-src"))
            await db.execute(delete(Employee).where(Employee.id.in_([pgc, mod, held, other])))
            await db.commit()
        await datacache.bust_pipeline()
        await datacache.bust_employees()


async def test_viewer_cannot_bulk_accept(client, admin_token):
    from tests.conftest import login_2fa
    r = await client.post("/api/v1/admin/users", headers=auth_headers(admin_token), json={
        "username": "bulkviewer", "password": "Password123", "email": "bv@example.com",
        "role": "viewer", "auth_mode": "otp"})
    assert r.status_code == 201, r.text
    token = await login_2fa(client, "bulkviewer", "Password123")
    res = await client.post("/api/v1/pipeline/bulk-accept", headers=auth_headers(token), json={})
    assert res.status_code == 403


async def test_bulk_accept_after_upload_files_leave_rows_and_flagged_rows(client, admin_token):
    """Roster upload -> Bulk accept: every matched row with data is filed - any leave type, any
    reader flag. Only a row that cannot be filed (unmatched / nothing read) stays."""
    h = auth_headers(admin_token)
    approval = "no manager approval evidence found - none"
    clean = await _emp(client, h, "RU-1", "Roster Clean", None, "Mgr")
    leave = await _emp(client, h, "RU-2", "Roster Leave", "ACME", "Mgr")
    flagged = await _emp(client, h, "RU-3", "Roster Flagged", "ACME", "Mgr")
    empty = await _emp(client, h, "RU-4", "Roster Empty", "ACME", "Mgr")
    pks = [clean, leave, flagged, empty]
    ids: dict[str, str] = {}
    disagree = "a second independent read disagreed on day(s) 20"
    try:
        async with SessionLocal() as db:
            for key, pk, name, blockers, buckets, flags in (
                ("clean", clean, "Roster Clean", [approval], {"annual": ["2034-05-04"], "sick": ["2034-05-05"]}, []),
                ("leave", leave, "Roster Leave", [approval], {"absent": ["2034-05-04"], "maternity": ["2034-05-05"]}, []),
                ("flagged", flagged, "Roster Flagged", [approval, "validation flags: " + disagree],
                 {"annual": ["2034-05-04"]}, [disagree]),
                ("empty", empty, "Roster Empty", [approval], {}, []),
            ):
                meta = _staged(pk, name, blockers)
                meta["staged"]["buckets"] = buckets
                meta["staged"]["flags"] = flags
                if key == "empty":
                    meta["staged"]["working_days"] = []
                    meta["staged"]["weekend_days"] = []
                t = PipelineFile(
                    filename=f"{name}.xlsx", source_kind="bulk", source_id="bulk:RU", thread_key="bulk:RU",
                    attachment_id=f"ru:{key}", status=PipelineStatus.NEEDS_REVIEW,
                    employee_id=key, employee_name=name, month=MONTH, year=YEAR, extraction_meta=meta)
                db.add(t)
                await db.flush()
                ids[key] = t.id
            await db.commit()

        dry = (await client.post("/api/v1/pipeline/bulk-accept", headers=h, json={
            "thread_key": "bulk:RU", "dry_run": True})).json()
        assert dry["ready"] == 3 and dry["held_count"] == 1
        assert "nothing was read" in dry["held"][0]["reasons"][0]

        res = (await client.post("/api/v1/pipeline/bulk-accept", headers=h, json={"thread_key": "bulk:RU"})).json()
        assert res["accepted"] == 3 and res["failed"] == []
        got = {i["employee_name"]: i for i in res["accepted_items"]}
        assert set(got) == {"Roster Clean", "Roster Leave", "Roster Flagged"}
        assert got["Roster Leave"]["buckets"] == {"absent": ["2034-05-04"], "maternity": ["2034-05-05"]}

        async with SessionLocal() as db:
            status = {k: (await db.get(PipelineFile, v)).status for k, v in ids.items()}
            flagged_row = await db.get(PipelineFile, ids["flagged"])
        assert status["clean"] == status["leave"] == status["flagged"] == PipelineStatus.SUCCESS
        assert status["empty"] == PipelineStatus.NEEDS_REVIEW
        assert "disagreed" in (flagged_row.resolution_note or ""), "the open flag is kept in the filing note"
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(TimesheetRecord).where(TimesheetRecord.matched_employee_pk.in_(pks)))
            await db.execute(delete(PipelineFile).where(PipelineFile.thread_key == "bulk:RU"))
            await db.execute(delete(Employee).where(Employee.id.in_(pks)))
            await db.commit()
        await datacache.bust_pipeline()
        await datacache.bust_employees()
