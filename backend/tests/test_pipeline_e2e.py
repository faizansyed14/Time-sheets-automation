"""End-to-end pipeline + employee flows, exercised through the authed API."""
from tests.conftest import auth_headers


async def _pdf(name="Tester", emp_id="E2E-1", month="March 2026", rows=(("2026-03-03", "Annual Leave"),)):
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "", 12)
    for ln in [f"Employee Name: {name}", f"Employee ID: {emp_id}", f"Month: {month}"]:
        pdf.cell(0, 8, ln, new_x="LMARGIN", new_y="NEXT")
    for d, s in rows:
        pdf.cell(0, 8, f"{d} {s}", new_x="LMARGIN", new_y="NEXT")
    out = pdf.output()
    return bytes(out) if isinstance(out, (bytes, bytearray)) else out.encode("latin-1")


async def test_upload_pipeline_and_tracker(client, admin_token, mock_vision_calls):
    h = auth_headers(admin_token)
    # add the employee to the matcher
    emp = await client.post("/api/v1/employee-matcher", headers=h,
                            json={"employee_id": "E2E-1", "name": "Tester", "location": "DXB"})
    assert emp.status_code == 201, emp.text

    data = await _pdf()
    mock_vision_calls([
        {"thread_summary": "x", "items": [{
            "source": "e2e.pdf", "is_timesheet": True, "kind": "timesheet",
            "employee_name": "Tester", "employee_id": "E2E-1",
            "period_hint": "March 2026", "evidence": "2026-03-03 Annual Leave",
            "manager_signature": False, "signature_evidence": "", "notes": "",
        }]},
        {"sheets": [{
            "source": "e2e.pdf", "employee_name": "Tester", "employee_id": "E2E-1",
            "month": 3, "year": 2026, "days_covered": 1, "period_type": "partial",
            "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
            "annual": ["2026-03-03"], "remote": [], "sick": [], "maternity": [],
            "unpaid": [], "absent": [], "public_holiday": [], "notes": "",
        }]},
    ])
    up = await client.post("/api/v1/upload", headers=h,
                           files={"files": ("e2e.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    result = up.json()[0]
    assert result["status"] == "needs_review"
    assert result["failure_code"] == "pending_review"
    assert result["employee_name"] == "Tester"
    assert result["record_id"] is None

    # pipeline tracker is paginated and lists the file
    pl = await client.get("/api/v1/pipeline?limit=50", headers=h)
    assert pl.status_code == 200
    page = pl.json()
    assert page["total"] >= 1
    assert any(f["filename"] == "e2e.pdf" for f in page["items"])


async def test_manual_entry_creates_record(client, admin_token):
    import json
    h = auth_headers(admin_token)
    emp = await client.post("/api/v1/employee-matcher", headers=h,
                            json={"employee_id": "MAN-1", "name": "Manual Person", "location": "DXB"})
    assert emp.status_code == 201, emp.text
    pk = emp.json()["id"]
    r = await client.post(
        "/api/v1/upload/manual", headers=h,
        data={"employee_pk": pk, "month": "3", "year": "2026",
              "buckets": json.dumps({"annual": ["2026-03-03"], "sick": ["2026-03-10"]}),
              "note": "entered by HR"},
        files={"files": ("manual.pdf", b"%PDF-1.4 manual", "application/pdf")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("success", "needs_review")
    assert body["employee_name"] == "Manual Person"
    assert body["record_id"]
    rec = await client.get(f"/api/v1/timesheets/{body['record_id']}", headers=h)
    assert rec.status_code == 200, rec.text
    assert "2026-03-03" in rec.json()["annual_leave_dates"]
    assert "2026-03-10" in rec.json()["sick_leave_dates"]


async def test_coverage_pagination_and_search(client, admin_token):
    h = auth_headers(admin_token)
    cov = await client.get("/api/v1/employees/coverage?year=2026&month=3&limit=5", headers=h)
    assert cov.status_code == 200
    body = cov.json()
    for key in ("total_employees", "submitted_this_month", "missing_this_month",
                "rows", "has_more", "filtered_total"):
        assert key in body
    # search hits the whole table
    s = await client.get("/api/v1/employees/coverage?year=2026&month=3&q=Tester", headers=h)
    assert s.status_code == 200
    assert any(r["employee_name"] == "Tester" for r in s.json()["rows"])


async def test_coverage_missing_vs_awaiting_review_vs_submitted(client, admin_token):
    """Missing / Submitted / Awaiting-review must exactly partition the active
    roster for a focus month. An employee who emailed a sheet the pipeline
    could read (a PipelineFile with extraction_meta.staged.employee_pk) but
    that hasn't been accepted into a TimesheetRecord yet must show as
    "awaiting review", never "missing" — missing means no email at all."""
    from sqlalchemy import delete
    from app.core import datacache
    from app.core.database import SessionLocal
    from app.models.pipeline_file import PipelineFile, PipelineStatus

    h = auth_headers(admin_token)
    YEAR, MONTH = 2031, 7  # a period no other test touches

    emp = await client.post("/api/v1/employee-matcher", headers=h,
                            json={"employee_id": "COV-1", "name": "Coverage Case", "location": "DXB"})
    assert emp.status_code == 201, emp.text
    pk = emp.json()["id"]

    async def _cov(**params):
        r = await client.get("/api/v1/employees/coverage", headers=h,
                             params={"year": YEAR, "month": MONTH, "limit": 500, **params})
        assert r.status_code == 200, r.text
        return r.json()

    before = await _cov()
    total = before["total_employees"]
    assert before["missing_this_month"] + before["submitted_this_month"] + before["awaiting_review_this_month"] == total
    row_before = next(r for r in before["rows"] if r["employee_pk"] == pk)
    assert row_before["awaiting_review_this_month"] is False

    async with SessionLocal() as db:
        db.add(PipelineFile(
            filename="sheet.pdf", source_kind="email", source_id="COV-msg-1",
            thread_key="COV-msg-1", attachment_id="pass1:covtest",
            status=PipelineStatus.NEEDS_REVIEW, employee_id="COV-1", employee_name="Coverage Case",
            month=MONTH, year=YEAR,
            extraction_meta={"staged": {"employee_pk": pk}},
        ))
        await db.commit()
    await datacache.bust_coverage()  # direct DB write bypasses the usual bust_pipeline() call

    try:
        mid = await _cov()
        assert mid["missing_this_month"] + mid["submitted_this_month"] + mid["awaiting_review_this_month"] == total
        assert mid["awaiting_review_this_month"] == before["awaiting_review_this_month"] + 1
        assert mid["missing_this_month"] == before["missing_this_month"] - 1
        assert mid["submitted_this_month"] == before["submitted_this_month"]
        row = next(r for r in mid["rows"] if r["employee_pk"] == pk)
        assert row["awaiting_review_this_month"] is True

        awaiting = await _cov(status="awaiting_review")
        assert any(r["employee_pk"] == pk for r in awaiting["rows"])
        missing = await _cov(status="missing")
        assert not any(r["employee_pk"] == pk for r in missing["rows"])
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.thread_key == "COV-msg-1"))
            await db.commit()
        await datacache.bust_coverage()


async def test_inbox_pagination(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/inbox?limit=3", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"items", "total", "limit", "offset", "has_more"}


async def test_content_endpoints_accept_query_token(client, admin_token):
    """PDF/image previews + downloads load via direct browser URLs that can't
    set headers, so the token is accepted as a ?token= query param."""
    # download-zip is protected; without any token -> 401
    no_auth = await client.get("/api/v1/files/download-zip")
    assert no_auth.status_code == 401
    # with the token in the query string -> 200 (browser-style load)
    with_q = await client.get(f"/api/v1/files/download-zip?token={admin_token}")
    assert with_q.status_code == 200
    assert with_q.headers["content-type"] == "application/zip"
    # an email attachment loads the same way
    inbox = await client.get("/api/v1/inbox?limit=1", headers=auth_headers(admin_token))
    items = inbox.json()["items"]
    if items:
        mid = items[0]["provider_message_id"]
        detail = await client.get(f"/api/v1/inbox/{mid}", headers=auth_headers(admin_token))
        atts = detail.json()["attachments"]
        if atts:
            aid = atts[0]["attachment_id"]
            url = f"/api/v1/inbox/{mid}/attachments/{aid}?token={admin_token}"
            att = await client.get(url)
            assert att.status_code == 200
