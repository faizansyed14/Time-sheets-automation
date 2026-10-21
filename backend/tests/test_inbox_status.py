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


# ---- New / Extracted / Ingested: one badge per thread state ----
# New  = no extraction run has read this message yet (a fresh reply in an
#        already-extracted thread is New again);
# Extracted = read by a run, something still awaiting review;
# Ingested  = read by a run and every staged item of the thread has been filed.
# Filing flips only the single message an item was extracted from, so the
# per-message status flag alone can't drive this — see _filed_thread_keys.

def _msg(pmid, conv, received, status=EmailStatus.NEW):
    return EmailMessage(
        provider_message_id=pmid, conversation_id=conv, sender_name="Badge Test",
        sender_email="zz-badge-test@example.com", subject="t", body_text="",
        attachments=[], to_recipients=[], cc_recipients=[], received_at=received,
        status=status)


def _item(source_id, conv, status, snapshot_at=None):
    from app.services.extract_email.constants import TAG_PREFIX
    meta = {"full_email_extract": {"snapshot_at": snapshot_at.isoformat()}} if snapshot_at else None
    return PipelineFile(
        filename="thread.eml", content_type="message/rfc822", source_kind="email",
        source_id=source_id, thread_key=conv, attachment_id=f"{TAG_PREFIX}:{source_id}",
        status=status, extraction_meta=meta)


async def test_inbox_filters_and_flags_match_the_new_extracted_ingested_badges(client, admin_token):
    from tests.conftest import auth_headers
    from app.models.pipeline_file import PipelineStatus

    now = dt.datetime.now(dt.timezone.utc)
    ids = ["BADGE-A", "BADGE-B", "BADGE-C", "BADGE-D-OLD", "BADGE-D-NEW"]
    async with SessionLocal() as db:
        db.add_all([
            _msg("BADGE-A", "BADGE-CONV-A", now - dt.timedelta(days=2)),            # never extracted
            _msg("BADGE-B", "BADGE-CONV-B", now - dt.timedelta(days=2)),            # extracted, awaiting review
            _msg("BADGE-C", "BADGE-CONV-C", now - dt.timedelta(days=2)),            # extracted, filed
            _msg("BADGE-D-OLD", "BADGE-CONV-D", now - dt.timedelta(days=3)),        # filed thread...
            _msg("BADGE-D-NEW", "BADGE-CONV-D", now - dt.timedelta(hours=1)),       # ...with a reply after the run
        ])
        db.add_all([
            _item("BADGE-B", "BADGE-CONV-B", PipelineStatus.NEEDS_REVIEW),
            _item("BADGE-C", "BADGE-CONV-C", PipelineStatus.SUCCESS),
            # snapshot_at is when the thread was actually READ (1 day ago); the
            # row's updated_at is "now" — it moved when the item was accepted,
            # which is AFTER the reply arrived. The reply must still be New.
            _item("BADGE-D-OLD", "BADGE-CONV-D", PipelineStatus.SUCCESS,
                  snapshot_at=now - dt.timedelta(days=1)),
        ])
        await db.commit()

    try:
        h = auth_headers(admin_token)

        async def ids_for(status):
            r = await client.get("/api/v1/inbox", headers=h,
                                 params={"status": status, "q": "zz-badge-test", "limit": 500})
            assert r.status_code == 200, r.text
            return {i["provider_message_id"] for i in r.json()["items"]}

        assert await ids_for("new") == {"BADGE-A", "BADGE-D-NEW"}
        assert await ids_for("extracted") == {"BADGE-B"}
        assert await ids_for("ingested") == {"BADGE-C", "BADGE-D-OLD"}

        # Each message is in exactly one of the three.
        sets = [await ids_for(s) for s in ("new", "extracted", "ingested")]
        assert sum(len(s) for s in sets) == len(set().union(*sets)) == 5

        r = await client.get("/api/v1/inbox", headers=h, params={"q": "zz-badge-test", "limit": 500})
        by = {i["provider_message_id"]: i for i in r.json()["items"]}
        assert by["BADGE-C"]["thread_ingested"] is True
        assert by["BADGE-B"]["thread_ingested"] is False
        assert by["BADGE-D-NEW"]["extract_email_at"] is None, "the unread reply must not look extracted"
        assert by["BADGE-D-OLD"]["extract_email_at"] is not None
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.source_id.in_(ids)))
            await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.in_(ids)))
            await db.commit()


async def test_a_thread_is_only_ingested_once_every_item_is_filed():
    from app.api.routes.inbox import _filed_thread_keys
    from app.models.pipeline_file import PipelineStatus

    now = dt.datetime.now(dt.timezone.utc)
    ids = ["FILED-1", "FILED-2"]
    async with SessionLocal() as db:
        m = _msg("FILED-1", "FILED-CONV", now - dt.timedelta(days=1))
        a = _item("FILED-1", "FILED-CONV", PipelineStatus.SUCCESS)
        b = _item("FILED-2", "FILED-CONV", PipelineStatus.NEEDS_REVIEW)
        db.add_all([m, a, b])
        await db.commit()
        try:
            assert await _filed_thread_keys(db, [m]) == set(), "one item still awaiting review"
            b.status = PipelineStatus.SUCCESS
            await db.commit()
            assert await _filed_thread_keys(db, [m]) == {"FILED-CONV"}
            # A message with no staged items at all is never "filed".
            lone = _msg("FILED-LONE", "FILED-LONE-CONV", now)
            assert await _filed_thread_keys(db, [lone]) == set()
        finally:
            await db.execute(delete(PipelineFile).where(PipelineFile.source_id.in_(ids)))
            await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.in_(ids)))
            await db.commit()


async def test_a_reply_that_arrived_before_the_accept_is_not_marked_extracted():
    """The thread watermark is the run's snapshot_at, not the item's updated_at:
    accepting bumps updated_at to 'now', which would otherwise swallow a reply
    that arrived after the run but before the accept."""
    from app.api.routes.inbox import _extract_email_times
    from app.models.pipeline_file import PipelineStatus

    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as db:
        old = _msg("WM-OLD", "WM-CONV", now - dt.timedelta(days=3))
        reply = _msg("WM-REPLY", "WM-CONV", now - dt.timedelta(hours=2))
        item = _item("WM-OLD", "WM-CONV", PipelineStatus.SUCCESS, snapshot_at=now - dt.timedelta(days=1))
        db.add_all([old, reply, item])
        await db.commit()
        try:
            times = await _extract_email_times(db, ["WM-OLD", "WM-REPLY"], rows=[old, reply])
            assert "WM-OLD" in times
            assert "WM-REPLY" not in times
        finally:
            await db.execute(delete(PipelineFile).where(PipelineFile.source_id == "WM-OLD"))
            await db.execute(delete(EmailMessage).where(
                EmailMessage.provider_message_id.in_(["WM-OLD", "WM-REPLY"])))
            await db.commit()


async def test_threaded_status_filter_is_judged_on_the_newest_message(client, admin_token):
    """The Inbox page lists one row per thread, showing its NEWEST message. A
    thread whose older message is ingested but whose newest message is an
    unread reply displays "New" — it must therefore be listed under New and NOT
    under Ingested, or the dropdown and the badge disagree."""
    from tests.conftest import auth_headers
    from app.models.pipeline_file import PipelineStatus

    now = dt.datetime.now(dt.timezone.utc)
    ids = ["THR-A", "THR-B", "THR-C", "THR-D-OLD", "THR-D-NEW"]
    async with SessionLocal() as db:
        db.add_all([
            _msg("THR-A", "THR-CONV-A", now - dt.timedelta(days=2)),
            _msg("THR-B", "THR-CONV-B", now - dt.timedelta(days=2)),
            _msg("THR-C", "THR-CONV-C", now - dt.timedelta(days=2)),
            _msg("THR-D-OLD", "THR-CONV-D", now - dt.timedelta(days=3)),
            _msg("THR-D-NEW", "THR-CONV-D", now - dt.timedelta(hours=1)),
        ])
        db.add_all([
            _item("THR-B", "THR-CONV-B", PipelineStatus.NEEDS_REVIEW),
            _item("THR-C", "THR-CONV-C", PipelineStatus.SUCCESS),
            _item("THR-D-OLD", "THR-CONV-D", PipelineStatus.SUCCESS,
                  snapshot_at=now - dt.timedelta(days=1)),
        ])
        await db.commit()
    try:
        h = auth_headers(admin_token)

        async def threads(status):
            r = await client.get("/api/v1/inbox/threads", headers=h,
                                 params={"status": status, "q": "zz-badge-test", "limit": 500})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j["total"] == len(j["items"]), "total must count threads, not messages"
            return j["items"]

        new = await threads("new")
        assert {i["thread_id"] for i in new} == {"THR-CONV-A", "THR-CONV-D"}
        # the thread row shows its NEWEST message — the unread reply
        assert {i["provider_message_id"] for i in new if i["thread_id"] == "THR-CONV-D"} == {"THR-D-NEW"}
        assert {i["thread_id"] for i in await threads("extracted")} == {"THR-CONV-B"}
        # THR-CONV-D has an ingested OLD message, but its newest is unread → not Ingested
        assert {i["thread_id"] for i in await threads("ingested")} == {"THR-CONV-C"}
        # search still matches any message and the unfiltered list has every thread once
        assert len(await threads("")) == 4
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.source_id.in_(ids)))
            await db.execute(delete(EmailMessage).where(EmailMessage.provider_message_id.in_(ids)))
            await db.commit()
