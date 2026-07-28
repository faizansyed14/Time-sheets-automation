"""End-to-end: a clean, fully-verified group is STAGED with an AI
recommendation (NEEDS_REVIEW, not filed) until a human Accepts. A group with
a blocker is held without the recommendation."""
from sqlalchemy import select

from tests._sheet_helpers import full_month_sheet

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.pipeline_file import PipelineStatus
from app.models.timesheet_record import TimesheetRecord
from app.services.extract_email.grouping import group_sheets
from app.services.extract_email.staging import stage_groups


async def _employee(db) -> Employee:
    emp = (await db.execute(select(Employee).where(
        Employee.employee_id == "E2506943"))).scalar_one_or_none()
    if not emp:
        emp = Employee(employee_id="E2506943", name="Bhargavi Prabhu",
                       location="DXB", account_manager="Test Manager")
        db.add(emp)
        await db.commit()
        await db.refresh(emp)
    return emp


def _weekend_dates(month: int, year: int) -> set[str]:
    import calendar
    import datetime as dt
    last = calendar.monthrange(year, month)[1]
    return {dt.date(year, month, d).isoformat() for d in range(1, last + 1)
            if dt.date(year, month, d).weekday() >= 5}


def _clean_sheet(emp) -> dict:
    weekend = _weekend_dates(6, 2026)
    sheet = full_month_sheet("TIMESHEET.pdf", 6, 2026,
                             public_holiday=["2026-06-15"], sick=["2026-06-19"])
    sheet["working_days"] = [d for d in sheet["working_days"] if d not in weekend]
    sheet["weekend_days"] = sorted(weekend - {"2026-06-15", "2026-06-19"})
    sheet["employee_name"] = emp.name
    sheet["employee_id"] = emp.employee_id
    return sheet


async def _clean_records(db, emp):
    for r in (await db.execute(select(TimesheetRecord).where(
            TimesheetRecord.matched_employee_pk == emp.id,
            TimesheetRecord.month == 6, TimesheetRecord.year == 2026))).scalars():
        await db.delete(r)
    await db.commit()


async def test_clean_group_is_staged_with_ai_recommendation():
    async with SessionLocal() as db:
        emp = await _employee(db)
        await _clean_records(db, emp)
        groups = await group_sheets(db, None, [_clean_sheet(emp)])
        assert len(groups) == 1
        staged = await stage_groups(
            db, source_kind="email", source_id="autotest-msg-1",
            raw_bytes=b"%PDF-fake", raw_name="bhargavi.pdf", content_type="application/pdf",
            groups=groups, approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "thread-two-pass", "model": "test-model", "calls": 2})
        t = staged[0]
        assert t.status == PipelineStatus.NEEDS_REVIEW, (t.status, t.failure_detail)
        assert t.extraction_meta["auto_accept"]["accepted"] is True, t.extraction_meta["auto_accept"]
        assert t.record_id is None
        assert t.raw_path is not None
        recs = (await db.execute(select(TimesheetRecord).where(
            TimesheetRecord.matched_employee_pk == emp.id,
            TimesheetRecord.month == 6, TimesheetRecord.year == 2026))).scalars().all()
        assert recs == []
        await _clean_records(db, emp)


async def test_pipeline_list_filters_ai_recommendation(client, admin_token):
    """Activity log can filter items the AI recommends accepting vs held."""
    from tests.conftest import auth_headers

    async with SessionLocal() as db:
        emp = await _employee(db)
        await _clean_records(db, emp)
        groups = await group_sheets(db, None, [_clean_sheet(emp)])
        staged = await stage_groups(
            db, source_kind="email", source_id="autotest-filter-1",
            raw_bytes=b"%PDF-fake", raw_name="bhargavi.pdf", content_type="application/pdf",
            groups=groups, approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "thread-two-pass", "model": "test-model", "calls": 2})
        recommended_id = staged[0].id
        assert staged[0].extraction_meta["auto_accept"]["accepted"] is True

        held_sheet = _clean_sheet(emp)
        # Break the day grid — one working day silently dropped.
        held_sheet["working_days"] = held_sheet["working_days"][:-1]
        held_groups = await group_sheets(db, None, [held_sheet])
        held = await stage_groups(
            db, source_kind="email", source_id="autotest-filter-2",
            raw_bytes=b"%PDF-fake", raw_name="held.pdf", content_type="application/pdf",
            groups=held_groups, approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "thread-two-pass", "model": "test-model", "calls": 2})
        held_id = held[0].id
        assert held[0].extraction_meta["auto_accept"]["accepted"] is False

    h = auth_headers(admin_token)
    r = await client.get(
        "/api/v1/pipeline?status=needs_review&auto_accepted=true", headers=h)
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()["items"]]
    assert recommended_id in ids
    assert held_id not in ids
    assert all(i["auto_accepted"] for i in r.json()["items"])

    r2 = await client.get("/api/v1/pipeline?auto_accepted=false", headers=h)
    assert r2.status_code == 200, r2.text
    assert recommended_id not in [i["id"] for i in r2.json()["items"]]

    async with SessionLocal() as db:
        emp = await _employee(db)
        await _clean_records(db, emp)


async def test_group_with_unaccounted_day_is_held_for_review():
    async with SessionLocal() as db:
        emp = await _employee(db)
        await _clean_records(db, emp)
        sheet = _clean_sheet(emp)
        sheet["working_days"] = sheet["working_days"][:-1]   # one day silently unaccounted
        groups = await group_sheets(db, None, [sheet])
        staged = await stage_groups(
            db, source_kind="email", source_id="autotest-msg-2",
            raw_bytes=b"%PDF-fake", raw_name="bhargavi.pdf", content_type="application/pdf",
            groups=groups, approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "thread-two-pass", "model": "test-model", "calls": 2})
        t = staged[0]
        assert t.status == PipelineStatus.NEEDS_REVIEW
        assert t.extraction_meta["auto_accept"]["accepted"] is False
        assert any("not accounted for" in b for b in t.extraction_meta["auto_accept"]["blockers"])
        await _clean_records(db, emp)
