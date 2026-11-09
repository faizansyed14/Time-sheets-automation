"""Each month shows ITS OWN health on the dashboard.

An employee with June (verified + approved) and July (flagged for review): June is green,
July is amber. One month's problem must not colour another month.
"""
from sqlalchemy import delete

from app.core import datacache
from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.timesheet_record import ApprovalStatus, TimesheetRecord, ValidationStatus
from tests.conftest import auth_headers

YEAR = 2035      # a year no other test touches


async def test_a_flagged_month_does_not_colour_the_clean_month(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": "MH-1", "name": "Month Health", "project": "MhProj",
        "account_manager": "Mh Manager", "location": "DXB"})
    assert r.status_code == 201, r.text
    pk = r.json()["id"]
    rec_ids = []
    try:
        async with SessionLocal() as db:
            for month, validation, approval in (
                (6, ValidationStatus.VERIFIED, ApprovalStatus.APPROVED),          # clean
                (7, ValidationStatus.MANUAL_REVIEW, ApprovalStatus.APPROVED),     # flagged
                (8, ValidationStatus.VERIFIED, ApprovalStatus.PENDING),           # clean data, not approved yet
            ):
                rec = TimesheetRecord(
                    employee_id="MH-1", employee_name="Month Health", matched_employee_pk=pk,
                    month=month, year=YEAR, validation_status=validation, approval_status=approval)
                db.add(rec)
                await db.flush()
                rec_ids.append(rec.id)
            await db.commit()
        await datacache.bust_employees()

        def row_for(month):
            async def go():
                resp = await client.get("/api/v1/employees/coverage", headers=h,
                                        params={"year": YEAR, "month": month, "project": "MhProj"})
                assert resp.status_code == 200, resp.text
                (row,) = [x for x in resp.json()["rows"] if x["employee_pk"] == pk]
                return row
            return go()

        june = await row_for(6)
        assert june["submitted_months"] == [6, 7, 8]
        assert june["issue_months"] == [7, 8]                  # June is NOT in it -> green
        assert june["focus_validation_status"] == "verified" and june["focus_approval_status"] == "approved"

        july = await row_for(7)
        assert july["focus_validation_status"] == "manual_review"
        assert 7 in july["issue_months"]
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(TimesheetRecord).where(TimesheetRecord.matched_employee_pk == pk))
            await db.execute(delete(Employee).where(Employee.id == pk))
            await db.commit()
        await datacache.bust_employees()
