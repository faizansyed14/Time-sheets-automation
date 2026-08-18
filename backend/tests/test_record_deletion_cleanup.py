"""Deleting a filed timesheet record must not leave the pipeline tracker that
filed it pointing at a dead record_id — the Pipeline page's "View record"
button used to link straight to a 404 forever after this (the record's own
detail page then spun/errored indefinitely too, since it had no "not found"
state either)."""
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile, PipelineStatus
from app.models.timesheet_record import TimesheetRecord
from app.services.pipeline.ingestion import ingest_manual_entry
from tests.conftest import auth_headers


async def _employee(db) -> Employee:
    row = (await db.execute(select(Employee).where(
        Employee.employee_id == "DEL-1", Employee.name == "Delete Test"))).scalar_one_or_none()
    if row is None:
        row = Employee(employee_id="DEL-1", name="Delete Test", location="DXB")
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def test_deleting_a_record_unlinks_and_relabels_its_pipeline_tracker(client, admin_token):
    async with SessionLocal() as db:
        emp = await _employee(db)
        rec, tracker = await ingest_manual_entry(
            db, employee_pk=emp.id, month=6, year=2026,
            buckets={"annual": ["2026-06-01"]},
            source_key="delete-cleanup-test", source_filename="sheet.pdf")
        # ingest_manual_entry's own tracker starts needs_review — mirror what
        # the real Accept-in-Compare-&-Fix flow does once a human accepts it
        # (pipeline.py's manual-fix route sets exactly these three fields).
        tracker.record_id = rec.id
        tracker.status = PipelineStatus.SUCCESS
        tracker.failure_code = None
        await db.commit()
        record_id = rec.id
        tracker_id = tracker.id

    h = auth_headers(admin_token)
    try:
        d = await client.delete(f"/api/v1/timesheets/{record_id}", headers=h)
        assert d.status_code == 200, d.text

        # the record itself is gone
        assert (await client.get(f"/api/v1/timesheets/{record_id}", headers=h)).status_code == 404

        # the tracker that filed it is un-linked and honestly relabelled —
        # never left pointing at a dead record_id.
        listing = await client.get(
            "/api/v1/pipeline", headers=h, params={"failure_code": "record_deleted"})
        assert listing.status_code == 200, listing.text
        row = next(i for i in listing.json()["items"] if i["id"] == tracker_id)
        assert row["record_id"] is None
        assert row["status"] == "failed"
        assert row["failure_code"] == "record_deleted"
        assert row["failure_label"] == "Record deleted"
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.id == tracker_id))
            await db.commit()


async def test_activity_log_can_hide_succeeded_files(client, admin_token):
    """The Activity log is for work that still needs someone — succeeded files
    are finished and would bury the rest, so the default view excludes them
    (?exclude_status=success) while the Success card still opens them."""
    async with SessionLocal() as db:
        emp = await _employee(db)
        rec, tracker = await ingest_manual_entry(
            db, employee_pk=emp.id, month=7, year=2026,
            buckets={"annual": ["2026-07-01"]},
            source_key="exclude-status-test", source_filename="done.pdf")
        tracker.record_id = rec.id
        tracker.status = PipelineStatus.SUCCESS
        tracker.failure_code = None
        await db.commit()
        tracker_id, record_id = tracker.id, rec.id

    h = auth_headers(admin_token)
    try:
        def ids(page):
            return [i["id"] for i in page.json()["items"]]

        # unfiltered: the succeeded file is there
        assert tracker_id in ids(await client.get("/api/v1/pipeline?limit=500", headers=h))
        # default Activity-log view: hidden
        hidden = await client.get(
            "/api/v1/pipeline?limit=500&exclude_status=success", headers=h)
        assert hidden.status_code == 200, hidden.text
        assert tracker_id not in ids(hidden)
        assert all(i["status"] != "success" for i in hidden.json()["items"])
        # Success card: still reachable
        assert tracker_id in ids(
            await client.get("/api/v1/pipeline?limit=500&status=success", headers=h))
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.id == tracker_id))
            await db.execute(delete(TimesheetRecord).where(TimesheetRecord.id == record_id))
            await db.commit()
