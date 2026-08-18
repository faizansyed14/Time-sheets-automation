"""Filing a record from an email-sourced pipeline item must flip the inbox
row to INGESTED — the staged flows (Extract Email / Run Extraction) accept via
the pipeline, not the legacy Accept decision."""
import datetime as dt

from sqlalchemy import delete

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


async def test_a_reply_that_arrives_after_the_last_extraction_is_not_marked_extracted():
    """Regression: the thread-level rollup in _extract_email_times used to
    stamp EVERY message in a conversation with the thread's last extraction
    timestamp, unconditionally — including a reply that arrived AFTER that
    run. That made a brand-new, never-read reply show the exact same
    "Extracted" badge as the old message in the inbox list/thread view,
    right up until someone actually opened the thread (the one place the
    received_at-vs-extracted-at comparison was already done correctly,
    client-side in Inbox.tsx). Auto Extract's own skip check was never
    affected by this — it already compared timestamps per-message — so this
    is purely about the badge lying to a reviewer scanning the list."""
    from app.api.routes.inbox import _extract_email_times
    from app.services.extract_email.constants import TAG_PREFIX

    async with SessionLocal() as db:
        conv = "REPLY-BUG-CONV-1"
        now = dt.datetime.now(dt.timezone.utc)
        old_msg = EmailMessage(
            provider_message_id="REPLY-BUG-OLD", conversation_id=conv,
            sender_name="S", sender_email="s@x.y", subject="t", body_text="",
            attachments=[], to_recipients=[], cc_recipients=[],
            received_at=now - dt.timedelta(days=2),
        )
        new_msg = EmailMessage(
            provider_message_id="REPLY-BUG-NEW", conversation_id=conv,
            sender_name="S", sender_email="s@x.y", subject="t", body_text="",
            attachments=[], to_recipients=[], cc_recipients=[],
            # Arrives strictly AFTER the extraction run below.
            received_at=now + dt.timedelta(days=1),
        )
        db.add_all([old_msg, new_msg])
        await db.commit()

        # The extraction run itself: tagged for this thread, its updated_at
        # lands at "now" (server default) — before the new reply's
        # received_at, after the old one's.
        t = PipelineFile(filename="thread.eml", content_type="message/rfc822",
                         source_kind="email", source_id="REPLY-BUG-OLD",
                         thread_key=conv, attachment_id=f"{TAG_PREFIX}:replybug")
        db.add(t)
        await db.commit()

        try:
            times = await _extract_email_times(
                db, ["REPLY-BUG-OLD", "REPLY-BUG-NEW"], rows=[old_msg, new_msg])
            assert "REPLY-BUG-OLD" in times, "the already-read message must show as extracted"
            assert "REPLY-BUG-NEW" not in times, (
                "a reply that arrived AFTER the last run must NOT show as extracted")
        finally:
            await db.delete(t)
            await db.execute(delete(EmailMessage).where(
                EmailMessage.provider_message_id.in_(["REPLY-BUG-OLD", "REPLY-BUG-NEW"])))
            await db.commit()
