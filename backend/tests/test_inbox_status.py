"""Filing a record from an email-sourced pipeline item must flip the inbox
row to INGESTED — the staged flows (Extract Email / Run Extraction) accept via
the pipeline, not the legacy Accept decision."""
from app.core.database import SessionLocal
from app.models.email_message import EmailMessage, EmailStatus
from app.models.pipeline_file import PipelineFile
from app.services.pipeline.ingestion import mark_source_email_ingested


async def test_accepting_email_item_marks_inbox_ingested():
    async with SessionLocal() as db:
        email = EmailMessage(provider_message_id="ING-TEST-1", sender_name="X",
                             sender_email="x@y.z", subject="s", body_text="",
                             attachments=[])
        t = PipelineFile(filename="f.eml", content_type="message/rfc822",
                         source_kind="email", source_id="ING-TEST-1",
                         attachment_id="__email_extract__:abc")
        db.add_all([email, t])
        await db.commit()

        await mark_source_email_ingested(db, t)
        await db.commit()
        await db.refresh(email)
        assert email.status == EmailStatus.INGESTED
        assert email.decided_at is not None

        # Remove the fixture rows — other tests walk the pipeline table and
        # would trip over a tracker that has no raw file behind it.
        await db.delete(t)
        await db.delete(email)
        await db.commit()


async def test_extracted_filter_lists_only_extract_email_runs(client, admin_token):
    from tests.conftest import auth_headers
    h = auth_headers(admin_token)
    # Run Extract Email on one mock message (keyless → engine fallback works).
    r = await client.post("/api/v1/inbox/MSG-0001/extract-full", headers=h)
    assert r.status_code == 200, r.text

    r = await client.get("/api/v1/inbox", params={"status": "extracted"}, headers=h)
    assert r.status_code == 200, r.text
    ids = [i["provider_message_id"] for i in r.json()["items"]]
    assert "MSG-0001" in ids
    # An email never extracted must not appear under the Extracted filter.
    r_all = await client.get("/api/v1/inbox", headers=h)
    never_extracted = [i["provider_message_id"] for i in r_all.json()["items"]
                       if i["extract_email_at"] is None]
    assert all(m not in ids for m in never_extracted)


async def test_non_email_tracker_is_a_noop():
    async with SessionLocal() as db:
        t = PipelineFile(filename="f.pdf", content_type="application/pdf",
                         source_kind="upload")
        db.add(t)
        await db.commit()
        await mark_source_email_ingested(db, t)  # must not raise or change anything
        await db.delete(t)
        await db.commit()
