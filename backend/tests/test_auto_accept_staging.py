"""End-to-end: a clean, fully-verified ADR group is STAGED with an AI
recommendation (NEEDS_REVIEW, not filed) until a human Accepts. A group with
a blocker is held without the recommendation."""
import calendar

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.pipeline_file import PipelineStatus
from app.models.timesheet_record import TimesheetRecord
from app.services.extract_email.staging import stage_groups


def _adr_text():
    weekend = {6, 7, 13, 14, 20, 21, 27, 28}
    lines = []
    for d in range(1, 31):
        tag = f"{d}-June-26"
        if d in weekend:
            lines.append(f"{tag} Saturday Weekend")
        elif d == 15:
            lines.append(f"{tag} Public Holiday Public Holiday")
        elif d == 19:
            lines.append(f"{tag} Sick Leave Sick Leave")
        else:
            lines.append(f"{tag} 08:00 AM 5:00 PM 9 9")
    return "\n".join(lines)


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


def _group(emp, buckets, sheets_text, overlap=None):
    return {
        "tag": "__email_extract__:autotest",
        "employee_pk": emp.id, "name": emp.name, "employee_id": emp.employee_id,
        "note": "matched", "month": 6, "year": 2026,
        "buckets": {**{b: [] for b in
                       ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")},
                    **buckets},
        "overlap_flags": overlap or [], "fold_notes": [],
        "sheets": [{"name": "TIMESHEET.pdf", "kind": "timesheet",
                    "employee_name": emp.name, "employee_id": emp.employee_id,
                    "month": 6, "year": 2026, "manager_signature": False,
                    "approval_evidence": "", "format_id": "alpha_adr_attendance",
                    "text": sheets_text,
                    "buckets": {**{b: [] for b in
                                   ("annual", "remote", "sick", "maternity", "unpaid",
                                    "absent", "public_holiday")}, **buckets}}],
    }


async def _clean_records(db, emp):
    for r in (await db.execute(select(TimesheetRecord).where(
            TimesheetRecord.matched_employee_pk == emp.id,
            TimesheetRecord.month == 6, TimesheetRecord.year == 2026))).scalars():
        await db.delete(r)
    await db.commit()


async def test_clean_adr_group_is_staged_with_ai_recommendation():
    async with SessionLocal() as db:
        emp = await _employee(db)
        await _clean_records(db, emp)
        g = _group(emp, {"public_holiday": ["2026-06-15"], "sick": ["2026-06-19"]}, _adr_text())
        staged = await stage_groups(
            db, source_kind="email", source_id="autotest-msg-1",
            raw_bytes=b"%PDF-fake", raw_name="bhargavi.pdf", content_type="application/pdf",
            groups=[g], approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "vision", "model": "gpt-4o", "calls": 1})
        t = staged[0]
        assert t.status == PipelineStatus.NEEDS_REVIEW, (t.status, t.failure_detail)
        assert t.extraction_meta["auto_accept"]["accepted"] is True
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
        staged = await stage_groups(
            db, source_kind="email", source_id="autotest-filter-1",
            raw_bytes=b"%PDF-fake", raw_name="bhargavi.pdf", content_type="application/pdf",
            groups=[_group(emp, {"public_holiday": ["2026-06-15"]}, _adr_text())],
            approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "vision", "model": "gpt-4o", "calls": 1})
        recommended_id = staged[0].id
        assert staged[0].extraction_meta["auto_accept"]["accepted"] is True

        held = await stage_groups(
            db, source_kind="email", source_id="autotest-filter-2",
            raw_bytes=b"%PDF-fake", raw_name="held.pdf", content_type="application/pdf",
            groups=[_group(emp, {"public_holiday": ["2026-06-15"]}, _adr_text(),
                           overlap=["Date 2026-06-15 claimed by two files — verify."])],
            approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "vision", "model": "gpt-4o", "calls": 1})
        held_id = held[0].id

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


async def test_group_with_validation_flag_is_held_for_review():
    async with SessionLocal() as db:
        emp = await _employee(db)
        await _clean_records(db, emp)
        g = _group(emp, {"public_holiday": ["2026-06-15"], "sick": ["2026-06-19"]},
                   _adr_text(), overlap=["Date 2026-06-15 claimed by two files — verify."])
        staged = await stage_groups(
            db, source_kind="email", source_id="autotest-msg-2",
            raw_bytes=b"%PDF-fake", raw_name="bhargavi.pdf", content_type="application/pdf",
            groups=[g], approval={"detected": False, "detail": "No approval."},
            run_meta={"method": "vision", "model": "gpt-4o", "calls": 1})
        t = staged[0]
        assert t.status == PipelineStatus.NEEDS_REVIEW
        assert t.extraction_meta["auto_accept"]["accepted"] is False
        assert any("validation" in b for b in t.extraction_meta["auto_accept"]["blockers"])
        await _clean_records(db, emp)
