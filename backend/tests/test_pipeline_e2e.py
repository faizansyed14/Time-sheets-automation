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


async def test_upload_pipeline_and_tracker(client, admin_token):
    h = auth_headers(admin_token)
    # add the employee to the matcher
    emp = await client.post("/api/v1/employee-matcher", headers=h,
                            json={"employee_id": "E2E-1", "name": "Tester", "location": "DXB"})
    assert emp.status_code == 201, emp.text

    data = await _pdf()
    up = await client.post("/api/v1/upload", headers=h,
                           files={"files": ("e2e.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    result = up.json()[0]
    assert result["status"] in ("success", "needs_review")
    assert result["employee_name"] == "Tester"

    # pipeline tracker is paginated and lists the file
    pl = await client.get("/api/v1/pipeline?limit=50", headers=h)
    assert pl.status_code == 200
    page = pl.json()
    assert page["total"] >= 1
    assert any(f["filename"] == "e2e.pdf" for f in page["items"])


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
