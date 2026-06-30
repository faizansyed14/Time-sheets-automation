"""Agentic chat — tool layer (deterministic, no LLM) + endpoints."""
import pytest

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.timesheet_record import TimesheetRecord
from app.services.agents import chat_tools
from tests.conftest import auth_headers


@pytest.fixture
async def seeded():
    """One employee with a May-2026 timesheet carrying a couple of sick days.
    Idempotent — resets state so it can run against the shared session DB."""
    from sqlalchemy import delete, select
    async with SessionLocal() as db:
        emp = (await db.execute(select(Employee).where(
            Employee.employee_id == "CHAT-1", Employee.name == "Faizan Test"))).scalar_one_or_none()
        if not emp:
            emp = Employee(employee_id="CHAT-1", name="Faizan Test", location="DXB")
            db.add(emp)
            await db.flush()
        await db.execute(delete(TimesheetRecord).where(TimesheetRecord.matched_employee_pk == emp.id))
        rec = TimesheetRecord(
            matched_employee_pk=emp.id, employee_id="CHAT-1", employee_name="Faizan Test",
            month=5, year=2026, sick_leave_dates=["2026-05-10"], annual_leave_dates=[],
        )
        db.add(rec)
        await db.commit()
        return {"emp_pk": emp.id, "rec_id": rec.id}


async def test_find_and_read_tools(seeded):
    async with SessionLocal() as db:
        found = await chat_tools.find_employees(db, "Faizan")
        assert found["count"] >= 1
        assert any(e["name"] == "Faizan Test" for e in found["employees"])

        sub = await chat_tools.check_submission(db, "Faizan Test", 5, 2026)
        assert sub["status"] == "ok" and sub["submitted"] is True

        sub_none = await chat_tools.check_submission(db, "Faizan Test", 6, 2026)
        assert sub_none["submitted"] is False

        cnt = await chat_tools.count_leaves(db, "Faizan Test", "sick", 5, 2026)
        assert cnt["status"] == "ok" and cnt["total"] == 1


async def test_update_add_set_and_clear_leaves(seeded):
    async with SessionLocal() as db:
        # add 26th May (day number) as sick leave
        add = await chat_tools.update_leaves(
            db, "Faizan Test", 5, 2026, "sick", mode="add", dates=["26"])
        assert add["status"] == "ok"
        assert "2026-05-26" in add["change"]["after"]
        assert add["change"]["added"] == ["2026-05-26"]

    async with SessionLocal() as db:
        # clear sick leaves — empties the bucket but keeps the record
        cleared = await chat_tools.update_leaves(
            db, "Faizan Test", 5, 2026, "sick", mode="clear")
        assert cleared["status"] == "ok"
        assert cleared["change"]["after"] == []
        assert set(cleared["change"]["removed"]) == {"2026-05-10", "2026-05-26"}

    async with SessionLocal() as db:
        # the record still exists
        from sqlalchemy import select
        rec = (await db.execute(select(TimesheetRecord).where(
            TimesheetRecord.id == seeded["rec_id"]))).scalar_one_or_none()
        assert rec is not None and (rec.sick_leave_dates or []) == []


async def test_update_unknown_employee_and_leave_type(seeded):
    async with SessionLocal() as db:
        bad = await chat_tools.update_leaves(
            db, "Nobody Here", 5, 2026, "sick", mode="clear")
        assert bad["status"] in ("not_found", "ambiguous")
        bad_type = await chat_tools.count_leaves(db, "Faizan Test", "telepathy", 5, 2026)
        assert bad_type["status"] == "unknown_leave_type"


async def test_suggestions_endpoint(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/agentic-chat/suggestions", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggestions"] and body["prompt_book"]
    assert "enabled" in body


async def test_chat_without_api_key_degrades_gracefully(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/agentic-chat", headers=h, json={
        "messages": [{"role": "user", "content": "How many sick leaves does Faizan have?"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    # No key configured in the test env → graceful message, no crash.
    assert body["error"] == "no_api_key"
    assert "AI provider" in body["answer"]


async def test_chat_eml_preview_endpoint(client, admin_token):
    """A chat-uploaded .eml can be parsed for inline preview (item 3 fix)."""
    from app.services.agents import upload_cache
    eml = (b"From: mgr@alpha.ae\r\nTo: timesheet@alpha.ae\r\n"
           b"Subject: Re: TIMESHEET May 2026\r\nContent-Type: text/plain\r\n\r\n"
           b"Please find the approval attached.\r\n")
    tok = upload_cache.put(eml, "approval.eml", "message/rfc822")
    h = auth_headers(admin_token)
    r = await client.get(f"/api/v1/agentic-chat/attachments/{tok}/eml-preview", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subject"].startswith("Re: TIMESHEET")
    assert "mgr@alpha.ae" in body["from_"]
