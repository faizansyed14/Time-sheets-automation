"""Extract selected — the scoped variant of Extract Email.

The old per-attachment "Run Extraction" engine path is gone: selecting
attachments now runs the SAME extraction pipeline as the Extract Email button,
restricted to the chosen sheets, and stages one pending-review item per
employee+month group. Nothing files a record until Accept in Compare & Fix.
"""
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.pipeline_file import FailureCode, PipelineStatus
from app.models.timesheet_record import TimesheetRecord
from app.seed import mock_data
from tests.conftest import auth_headers


async def test_extract_selected_stages_pending_item(client, admin_token):
    h = auth_headers(admin_token)
    ts_aid = mock_data.attachment_id("MSG-0001", "ts")

    r = await client.post(
        "/api/v1/inbox/MSG-0001/extract-full", headers=h,
        json={"attachment_ids": [ts_aid], "extract_body": False})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["groups"] >= 1
    assert len(res["staged"]) == res["groups"]
    item = res["staged"][0]
    # Staged for review — extracted but not yet filed.
    assert item["status"] == PipelineStatus.NEEDS_REVIEW
    assert item["failure_code"] == FailureCode.PENDING_REVIEW
    assert item["record_id"] is None
    assert item["can_resolve_assign"] is True          # Compare & Fix button shows
    staged_meta = item["extraction_meta"]["staged"]
    assert "buckets" in staged_meta
    assert sum(len(v) for v in staged_meta["buckets"].values()) >= 1

    # No timesheet record was created by staging.
    async with SessionLocal() as db:
        recs = (await db.execute(select(TimesheetRecord).where(
            TimesheetRecord.source_email_id == "MSG-0001"))).scalars().all()
        assert recs == []

    # Re-running is idempotent (same group tag reuses the pending tracker).
    r2 = await client.post(
        "/api/v1/inbox/MSG-0001/extract-full", headers=h,
        json={"attachment_ids": [ts_aid]})
    assert r2.status_code == 200
    assert r2.json()["staged"][0]["id"] == item["id"]


async def test_extract_selected_unknown_email_404(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/inbox/NOPE/extract-full", headers=h,
                          json={"attachment_ids": []})
    assert r.status_code == 404


async def test_direct_accept_decision_is_rejected(client, admin_token):
    """The legacy accept-and-ingest decision is gone — everything goes through
    Extract Email + Compare & Fix review."""
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/inbox/MSG-0001/decision", headers=h,
                          json={"accepted": True})
    assert r.status_code == 400
    assert "Extract Email" in r.json()["detail"]


async def test_opening_email_returns_detail(client, admin_token):
    """Opening an email returns the detail with attachments (no AI check runs)."""
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/inbox/MSG-0001", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider_message_id"] == "MSG-0001"
    assert "attachments" in body
