"""Accepting a SECOND sheet for the same employee+month must UNION its leaves
with the first, never overwrite them.

Real case: one email carries an attendance sheet (annual + sick leave) AND a
separate sick-leave certificate (sick only). Accepting the timesheet, then the
certificate, must keep the annual leaves — the bug replaced them because both
manual Accepts collided on a single hardcoded 'manual_entry' source key.
"""
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.timesheet_record import TimesheetRecord
from app.services.pipeline.ingestion import ingest_manual_entry


async def _employee(db) -> Employee:
    emp = (await db.execute(select(Employee).where(
        Employee.employee_id == "E2406747"))).scalar_one_or_none()
    if not emp:
        emp = Employee(employee_id="E2406747", name="Albaraa Alshahhoud",
                       location="DXB", account_manager="Adel Al Hosani")
        db.add(emp)
        await db.commit()
        await db.refresh(emp)
    return emp


async def test_second_sheet_unions_not_overwrites():
    async with SessionLocal() as db:
        emp = await _employee(db)
        # Clean slate for this employee/month.
        for r in (await db.execute(select(TimesheetRecord).where(
                TimesheetRecord.matched_employee_pk == emp.id,
                TimesheetRecord.month == 6, TimesheetRecord.year == 2026))).scalars():
            await db.delete(r)
        await db.commit()

        # 1) Accept the ATTENDANCE SHEET: annual + sick + public holiday.
        rec1, _ = await ingest_manual_entry(
            db, employee_pk=emp.id, month=6, year=2026,
            buckets={
                "annual": ["2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19",
                           "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26"],
                "sick": ["2026-06-08", "2026-06-09"],
                "public_holiday": ["2026-06-15"],
            },
            source_key="email::attendance-sheet.pdf", source_filename="attendance.pdf")
        assert len(rec1.annual_leave_dates) == 9

        # 2) Accept the SICK-LEAVE CERTIFICATE (sick only) for the SAME period.
        rec2, _ = await ingest_manual_entry(
            db, employee_pk=emp.id, month=6, year=2026,
            buckets={"sick": ["2026-06-08", "2026-06-09"]},
            source_key="email::sick-certificate.pdf", source_filename="sick.pdf")

        # The annual leaves from sheet 1 MUST survive; sick stays deduped.
        assert rec2.id == rec1.id, "should update the same monthly record"
        assert sorted(rec2.annual_leave_dates) == [
            "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19",
            "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26"], \
            "annual leaves must NOT be wiped by the second sheet"
        assert sorted(rec2.sick_leave_dates) == ["2026-06-08", "2026-06-09"]
        assert rec2.public_holiday_dates == ["2026-06-15"]


async def test_reaccepting_same_sheet_replaces_its_own_dates():
    """Re-accepting the SAME sheet (same key) with edited dates replaces that
    sheet's contribution — it does not double or accumulate."""
    async with SessionLocal() as db:
        emp = await _employee(db)
        for r in (await db.execute(select(TimesheetRecord).where(
                TimesheetRecord.matched_employee_pk == emp.id,
                TimesheetRecord.month == 7, TimesheetRecord.year == 2026))).scalars():
            await db.delete(r)
        await db.commit()

        rec, _ = await ingest_manual_entry(
            db, employee_pk=emp.id, month=7, year=2026,
            buckets={"annual": ["2026-07-01", "2026-07-02"]},
            source_key="email::sheet.pdf", source_filename="s.pdf")
        assert sorted(rec.annual_leave_dates) == ["2026-07-01", "2026-07-02"]

        # Same key, corrected dates → replaces (not 4 dates).
        rec2, _ = await ingest_manual_entry(
            db, employee_pk=emp.id, month=7, year=2026,
            buckets={"annual": ["2026-07-03"]},
            source_key="email::sheet.pdf", source_filename="s.pdf")
        assert sorted(rec2.annual_leave_dates) == ["2026-07-03"]
