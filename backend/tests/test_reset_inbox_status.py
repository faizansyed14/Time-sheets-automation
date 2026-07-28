"""reset_inbox_status — resetting the inbox must make it genuinely look
untouched, including the two incremental-extraction caches
(EmailMessage.extracted_sheets / .thread_summary). Both are deliberately
durable across an ORDINARY resync (a reply weeks later should still skip
re-reading what's unchanged), but surviving an explicit RESET meant the next
extraction silently skipped Pass 2 ("served from cache") and the inbox kept
showing a stale thread summary — the bug this locks in a fix for.
"""
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.email_message import EmailMessage, EmailStatus
from app.models.pipeline_file import PipelineFile


async def _cleanup(db, msg_id: str) -> None:
    for pf in (await db.execute(select(PipelineFile).where(
            PipelineFile.source_kind == "email", PipelineFile.source_id == msg_id))).scalars():
        await db.delete(pf)
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == msg_id))).scalar_one_or_none()
    if row:
        await db.delete(row)
    await db.commit()


async def test_reset_clears_cached_sheets_and_thread_summary(monkeypatch):
    from app.seed import reset_inbox_status

    msg_id = "reset-test-msg-1"
    async with SessionLocal() as db:
        await _cleanup(db, msg_id)
        row = EmailMessage(
            provider_message_id=msg_id, sender_name="Employee", sender_email="employee@alpha.ae",
            subject="TIMESHEET June", received_at=datetime.now(timezone.utc),
            body_text="See attached.", attachments=[],
            status=EmailStatus.INGESTED,
            extracted_sheets={"a" * 64: {"filename": "sheet.pdf", "at": "2026-01-01T00:00:00+00:00",
                                         "model": "test", "prompt_version": "v1", "sheet": {}}},
            thread_summary={"headline": "stale summary", "status": "approved"},
        )
        db.add(row)
        await db.commit()

        assert row.extracted_sheets and row.thread_summary  # sanity — the bug's precondition

        monkeypatch.setattr(sys, "argv", ["reset_inbox_status.py"])
        await reset_inbox_status.main()

        await db.refresh(row)
        assert row.status == EmailStatus.NEW
        assert row.extracted_sheets is None, "reset must clear the cached-attachment digests"
        assert row.thread_summary is None, "reset must clear the carried-forward thread summary"

        await _cleanup(db, msg_id)


async def test_dry_run_reports_but_does_not_clear(monkeypatch, capsys):
    from app.seed import reset_inbox_status

    msg_id = "reset-test-msg-2"
    async with SessionLocal() as db:
        await _cleanup(db, msg_id)
        row = EmailMessage(
            provider_message_id=msg_id, sender_name="Employee", sender_email="employee@alpha.ae",
            subject="TIMESHEET June", received_at=datetime.now(timezone.utc),
            body_text="See attached.", attachments=[],
            status=EmailStatus.INGESTED,
            extracted_sheets={"b" * 64: {"filename": "sheet.pdf", "at": "2026-01-01T00:00:00+00:00",
                                         "model": "test", "prompt_version": "v1", "sheet": {}}},
            thread_summary={"headline": "stale summary"},
        )
        db.add(row)
        await db.commit()

        monkeypatch.setattr(sys, "argv", ["reset_inbox_status.py", "--dry-run"])
        await reset_inbox_status.main()
        out = capsys.readouterr().out
        # The dirty-email query is intentionally global (a "reset the whole
        # inbox" operation), so other tests' leftover rows in the shared test
        # DB can add to these counts — assert at least this row's contribution
        # is detected, not an exact total.
        import re
        cached_count = int(re.search(r"cached attachment digests to clear: (\d+)", out).group(1))
        summary_count = int(re.search(r"thread summaries to clear: (\d+)", out).group(1))
        assert cached_count >= 1
        assert summary_count >= 1
        assert "Dry run" in out

        await db.refresh(row)
        assert row.status == EmailStatus.INGESTED, "dry-run must not write anything"
        assert row.extracted_sheets is not None

        await _cleanup(db, msg_id)
