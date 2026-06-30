"""Run Extraction → stage to pipeline as needs-review (no record yet)."""
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.pipeline_file import FailureCode, PipelineStatus
from app.models.timesheet_record import TimesheetRecord
from app.seed import mock_data
from tests.conftest import auth_headers


async def test_stage_extraction_creates_pending_pipeline_item(client, admin_token):
    h = auth_headers(admin_token)
    ts_aid = mock_data.attachment_id("MSG-0001", "ts")

    r = await client.post(
        "/api/v1/inbox/MSG-0001/stage-extraction", headers=h,
        json={"attachment_ids": [ts_aid], "extract_body": False})
    assert r.status_code == 200, r.text
    staged = r.json()
    assert len(staged) == 1
    item = staged[0]
    # Staged for review — extracted but not yet filed.
    assert item["status"] == PipelineStatus.NEEDS_REVIEW
    assert item["failure_code"] == FailureCode.PENDING_REVIEW
    assert item["record_id"] is None
    assert item["can_resolve_assign"] is True          # Compare & Fix button shows
    assert item["month"] == 1 and item["year"] == 2026
    staged_meta = item["extraction_meta"]["staged"]
    assert "buckets" in staged_meta
    assert sum(len(v) for v in staged_meta["buckets"].values()) >= 1

    # No timesheet record was created by staging.
    async with SessionLocal() as db:
        recs = (await db.execute(select(TimesheetRecord).where(
            TimesheetRecord.source_email_id == "MSG-0001"))).scalars().all()
        assert recs == []

    # Re-running is idempotent (reuses the pending tracker, no duplicate).
    r2 = await client.post(
        "/api/v1/inbox/MSG-0001/stage-extraction", headers=h,
        json={"attachment_ids": [ts_aid]})
    assert r2.json()[0]["id"] == item["id"]


async def test_stage_extraction_unknown_email_404(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/inbox/NOPE/stage-extraction", headers=h,
                          json={"attachment_ids": []})
    assert r.status_code == 404


async def test_opening_email_with_docs_auto_runs_ai_check(client, admin_token):
    """Opening an email that has document attachments auto-runs the AI check."""
    h = auth_headers(admin_token)
    r = await client.get("/api/v1/inbox/MSG-0001", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # The detail carries the AI-check result (run on open — no batch endpoint).
    assert body["ai_check"] is not None
