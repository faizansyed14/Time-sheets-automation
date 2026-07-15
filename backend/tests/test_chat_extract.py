"""Chat upload → grounded extraction + ephemeral preview (no persistence)."""
import pytest

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.services.agents import upload_cache
from app.services.agents.extract_service import extract_from_upload
from app.services.email_provider.mock_provider import _render_timesheet_pdf
from tests.conftest import auth_headers

_CASE = {
    "slot": "ts", "doc": "pdf",
    "emp_id": "EMP-7001", "emp_name": "Upload Tester",
    "month": 5, "year": 2026,
    "sick": ["2026-05-24", "2026-05-25", "2026-05-26"],
    "annual": ["2026-05-12"],
}


def _pdf() -> bytes:
    return _render_timesheet_pdf(_CASE)


async def test_extract_from_upload_is_grounded(seed_employee_7001):
    async with SessionLocal() as db:
        result = await extract_from_upload(
            db, filename="timesheet.pdf", content_type="application/pdf", data=_pdf())
    assert result["status"] == "ok"
    assert result["extracted_employee_id"] == "EMP-7001"
    assert result["month"] == 5 and result["year"] == 2026
    # Dates come from the validated pipeline, not a guess.
    assert result["counts"]["Sick leave"] == 3
    assert "2026-05-26" in result["leaves"]["Sick leave"]
    assert result["matched_employee"] and result["matched_employee"]["employee_id"] == "EMP-7001"


async def test_extract_rejects_unsupported_type():
    async with SessionLocal() as db:
        result = await extract_from_upload(
            db, filename="note.txt", content_type="text/plain", data=b"hello")
    assert result["status"] == "unsupported_type"


async def test_upload_cache_roundtrip_and_expiry():
    tok = await upload_cache.put(b"abc", "x.pdf", "application/pdf")
    entry = await upload_cache.get(tok)
    assert entry and entry.data == b"abc" and entry.filename == "x.pdf"
    assert await upload_cache.get("nope") is None


async def test_extract_endpoint_and_ephemeral_preview(client, admin_token, seed_employee_7001):
    h = auth_headers(admin_token)
    files = {"file": ("timesheet.pdf", _pdf(), "application/pdf")}
    r = await client.post("/api/v1/agentic-chat/extract", headers=h, files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["token"]
    assert body["counts"]["Sick leave"] == 3

    # Preview serves the bytes back from the ephemeral store.
    p = await client.get(f"/api/v1/agentic-chat/attachments/{body['token']}", headers=h)
    assert p.status_code == 200
    assert p.content[:4] == b"%PDF"

    # Unknown token → 404 (nothing persisted).
    miss = await client.get("/api/v1/agentic-chat/attachments/does-not-exist", headers=h)
    assert miss.status_code == 404


async def test_extract_endpoint_rejects_bad_extension(client, admin_token):
    h = auth_headers(admin_token)
    files = {"file": ("note.txt", b"hello", "text/plain")}
    r = await client.post("/api/v1/agentic-chat/extract", headers=h, files=files)
    assert r.status_code == 400


@pytest.fixture
async def seed_employee_7001():
    """Matcher entry so the extracted sheet resolves to a real employee."""
    from sqlalchemy import select
    async with SessionLocal() as db:
        exists = (await db.execute(select(Employee).where(
            Employee.employee_id == "EMP-7001", Employee.name == "Upload Tester"))).scalar_one_or_none()
        if not exists:
            db.add(Employee(employee_id="EMP-7001", name="Upload Tester", location="DXB"))
            await db.commit()
