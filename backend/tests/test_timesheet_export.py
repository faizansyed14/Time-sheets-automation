"""Timesheet period export — XLSX download + by-period preview API."""
from io import BytesIO

from openpyxl import load_workbook

from app.models.employee import Employee
from app.models.timesheet_record import TimesheetRecord, ValidationStatus
from tests.conftest import auth_headers


async def test_export_by_period_lists_all_matcher_employees(client, admin_token):
    from app.core.database import SessionLocal

    h = auth_headers(admin_token)
    async with SessionLocal() as db:
        emp = Employee(
            employee_id="E-NO-SUB",
            name="Not Submitted Yet",
            account_manager="Mgr",
            location="DXB",
        )
        db.add(emp)
        await db.commit()
        pk = emp.id

    r = await client.get("/api/v1/timesheets/by-period", params={"month": 3, "year": 2099}, headers=h)
    assert r.status_code == 200, r.text
    row = next((x for x in r.json() if x["employee_id"] == "E-NO-SUB"), None)
    assert row is not None
    assert row["has_record"] is False
    assert row["annual_leave_dates"] == []

    async with SessionLocal() as db:
        obj = await db.get(Employee, pk)
        if obj:
            await db.delete(obj)
            await db.commit()


async def test_export_xlsx_contains_leave_dates(client, admin_token):
    from app.core.database import SessionLocal

    h = auth_headers(admin_token)
    async with SessionLocal() as db:
        emp = Employee(
            employee_id="E-EXP-1",
            name="Export Tester",
            account_manager="Manager A",
            location="AUH",
        )
        db.add(emp)
        await db.flush()
        rec = TimesheetRecord(
            matched_employee_pk=emp.id,
            employee_id="E-EXP-1",
            employee_name="Export Tester",
            account_manager="Manager A",
            month=6,
            year=2026,
            annual_leave_dates=["2026-06-04", "2026-06-05"],
            sick_leave_dates=["2026-06-12"],
            validation_status=ValidationStatus.VERIFIED,
        )
        db.add(rec)
        await db.commit()
        rid = rec.id
        pk = emp.id

    preview = await client.get(
        "/api/v1/timesheets/by-period", params={"month": 6, "year": 2026}, headers=h,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    row = next(r for r in body if r["employee_id"] == "E-EXP-1")
    assert row["id"] == rid
    assert row["has_record"] is True
    assert row["annual_leave_dates"] == ["2026-06-04", "2026-06-05"]
    assert row["sick_leave_dates"] == ["2026-06-12"]

    xlsx = await client.get("/api/v1/timesheets/export", params={"month": 6, "year": 2026}, headers=h)
    assert xlsx.status_code == 200, xlsx.text
    assert "spreadsheetml" in xlsx.headers["content-type"]

    wb = load_workbook(BytesIO(xlsx.content), read_only=True)
    ws = wb.active
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    data_rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert any(r[1] == "Export Tester" for r in data_rows)
    tester = next(r for r in data_rows if r[1] == "Export Tester")
    annual_dates_col = headers.index("Annual Leave Dates")
    assert "2026-06-04" in (tester[annual_dates_col] or "")

    async with SessionLocal() as db:
        rec_obj = await db.get(TimesheetRecord, rid)
        if rec_obj:
            await db.delete(rec_obj)
        emp_obj = await db.get(Employee, pk)
        if emp_obj:
            await db.delete(emp_obj)
        await db.commit()


async def test_export_splits_personal_and_work_email_and_adds_received_stored_columns(client, admin_token):
    """The export used to show one merged "Email" column and said nothing
    about timing. It must now show Personal/Work email separately, plus when
    the pipeline first received something for this employee this period
    (from PipelineFile) and when it was actually filed (TimesheetRecord.
    created_at) — a "Received & Not Stored" employee gets the first without
    the second."""
    from sqlalchemy import delete

    from app.core.database import SessionLocal
    from app.models.pipeline_file import PipelineFile, PipelineStatus

    h = auth_headers(admin_token)
    YEAR, MONTH = 2027, 4  # a period no other test touches

    async with SessionLocal() as db:
        stored_emp = Employee(
            employee_id="E-STORED",
            name="Stored Person",
            account_manager="Manager A",
            location="AUH",
            personal_email="stored.person@gmail.com",
            work_email="stored.person@company.com",
        )
        pending_emp = Employee(
            employee_id="E-PENDING",
            name="Pending Person",
            account_manager="Manager A",
            location="AUH",
            personal_email="pending.person@gmail.com",
            work_email="pending.person@company.com",
        )
        db.add_all([stored_emp, pending_emp])
        await db.flush()
        stored_pk, pending_pk = stored_emp.id, pending_emp.id

        db.add(PipelineFile(
            filename="stored.pdf", source_kind="email", source_id="EXP-msg-stored",
            thread_key="EXP-msg-stored", attachment_id="pass1:expstored",
            status=PipelineStatus.SUCCESS, employee_id="E-STORED", employee_name="Stored Person",
            month=MONTH, year=YEAR,
            extraction_meta={"staged": {"employee_pk": stored_pk}},
        ))
        db.add(PipelineFile(
            filename="pending.pdf", source_kind="email", source_id="EXP-msg-pending",
            thread_key="EXP-msg-pending", attachment_id="pass1:exppending",
            status=PipelineStatus.NEEDS_REVIEW, employee_id="E-PENDING", employee_name="Pending Person",
            month=MONTH, year=YEAR,
            extraction_meta={"staged": {"employee_pk": pending_pk}},
        ))
        rec = TimesheetRecord(
            matched_employee_pk=stored_pk,
            employee_id="E-STORED",
            employee_name="Stored Person",
            account_manager="Manager A",
            month=MONTH,
            year=YEAR,
            validation_status=ValidationStatus.VERIFIED,
        )
        db.add(rec)
        await db.commit()
        rid = rec.id

    try:
        preview = await client.get(
            "/api/v1/timesheets/by-period", params={"month": MONTH, "year": YEAR}, headers=h,
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        stored_out = next(r for r in body if r["employee_id"] == "E-STORED")
        assert stored_out["personal_email"] == "stored.person@gmail.com"
        assert stored_out["work_email"] == "stored.person@company.com"
        assert stored_out["received_at"]  # pipeline received something
        assert stored_out["stored_at"]    # and it was filed
        assert "employee_email" not in stored_out  # old merged field is gone

        pending_out = next(r for r in body if r["employee_id"] == "E-PENDING")
        assert pending_out["personal_email"] == "pending.person@gmail.com"
        assert pending_out["work_email"] == "pending.person@company.com"
        assert pending_out["received_at"]        # received...
        assert pending_out["stored_at"] is None  # ...but never filed

        xlsx = await client.get("/api/v1/timesheets/export", params={"month": MONTH, "year": YEAR}, headers=h)
        assert xlsx.status_code == 200, xlsx.text

        wb = load_workbook(BytesIO(xlsx.content), read_only=True)
        ws = wb.active
        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        for col in ("Personal Email", "Work Email", "Timesheet Received", "Timesheet Stored"):
            assert col in headers, headers
        assert "Email" not in headers  # the old merged column is gone

        data_rows = list(ws.iter_rows(min_row=2, values_only=True))
        personal_col = headers.index("Personal Email")
        work_col = headers.index("Work Email")
        received_col = headers.index("Timesheet Received")
        stored_col = headers.index("Timesheet Stored")

        stored_row = next(r for r in data_rows if r[1] == "Stored Person")
        assert stored_row[personal_col] == "stored.person@gmail.com"
        assert stored_row[work_col] == "stored.person@company.com"
        assert stored_row[received_col]  # the pipeline received something
        assert stored_row[stored_col]    # and it was filed

        pending_row = next(r for r in data_rows if r[1] == "Pending Person")
        assert pending_row[personal_col] == "pending.person@gmail.com"
        assert pending_row[work_col] == "pending.person@company.com"
        assert pending_row[received_col]        # received...
        assert not pending_row[stored_col]       # ...but never filed
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(
                PipelineFile.thread_key.in_(["EXP-msg-stored", "EXP-msg-pending"])))
            rec_obj = await db.get(TimesheetRecord, rid)
            if rec_obj:
                await db.delete(rec_obj)
            for emp_id in ("E-STORED", "E-PENDING"):
                obj = await db.get(Employee, stored_pk if emp_id == "E-STORED" else pending_pk)
                if obj:
                    await db.delete(obj)
            await db.commit()
