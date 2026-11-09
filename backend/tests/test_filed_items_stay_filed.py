"""A month that is already filed is not bounced back to review by a harmless reply.

Once an item is accepted (a record exists), every later message in its thread re-runs
the thread. If that complete re-read gives exactly the same result for the month, the
item stays filed (only its "checked up to" mark moves, so the thread is not re-read
forever). If the result changed - new days, a new sheet - it IS reopened, with the
reason on it.
"""
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStatus
from app.services.extract_email.grouping import group_sheets
from app.services.extract_email.staging import stage_groups

KEY = "filed-stays-filed"
META = {"method": "thread-two-pass", "model": "m", "errors": [], "warnings": [], "incomplete": False}
APPROVAL = {"detected": True, "where": "sheet", "detail": "ok"}


def _sheet(days, name="week.pdf"):
    return {"kind": "timesheet", "name": name, "employee_name": "Filed Person", "employee_id": "E-FILED",
            "month": 6, "year": 2026, "period_type": "week", "days_covered": len(days), "missing_days": [],
            "working_days": [f"2026-06-{d:02d}" for d in days], "weekend_days": [], "uncertain_days": [],
            "annual": [], "remote": [], "sick": [], "maternity": [], "unpaid": [], "absent": [],
            "public_holiday": [], "other": [], "notes": "", "manager_signature": True,
            "approval_evidence": "stamp", "approval_named_only": False}


async def _stage(sheets, *, snap="2026-06-10T10:00:00+00:00", **over):
    async with SessionLocal() as db:
        groups = await group_sheets(db, None, sheets)
        return await stage_groups(
            db, source_kind="email", source_id="filed-msg", raw_bytes=b"raw", raw_name="x.eml",
            content_type="message/rfc822", groups=groups, approval=APPROVAL,
            run_meta={**META, "snapshot_at": snap, **over}, thread_key=KEY)


async def _cleanup():
    async with SessionLocal() as db:
        for t in (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == KEY))).scalars():
            await db.delete(t)
        await db.commit()


async def _file_it():
    """Pretend the reviewer accepted the item (what the Accept route sets)."""
    async with SessionLocal() as db:
        (t,) = (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == KEY))).scalars().all()
        t.status = PipelineStatus.SUCCESS
        t.failure_code = None
        t.failure_detail = None
        t.record_id = "record-1"
        t.raw_path = None
        await db.commit()


async def _item():
    async with SessionLocal() as db:
        return (await db.execute(select(PipelineFile).where(PipelineFile.thread_key == KEY))).scalars().one()


async def test_a_reply_that_changes_nothing_leaves_a_filed_month_filed():
    await _cleanup()
    try:
        await _stage([_sheet([1, 2, 3])])
        await _file_it()
        await _stage([_sheet([1, 2, 3])], snap="2026-06-20T10:00:00+00:00")     # "Thanks!" arrived
        t = await _item()
        assert t.status == PipelineStatus.SUCCESS and t.record_id == "record-1"
        assert t.failure_code is None and t.raw_path is None
        assert t.extraction_meta["full_email_extract"]["snapshot_at"] == "2026-06-20T10:00:00+00:00"
        assert any("stays filed" in (e.get("detail") or "") for e in t.events)
        await _stage([_sheet([1, 2, 3])], snap="2026-06-25T10:00:00+00:00")     # and another reply
        assert (await _item()).status == PipelineStatus.SUCCESS
    finally:
        await _cleanup()


async def test_new_days_or_a_new_sheet_reopen_a_filed_month_with_the_reason():
    await _cleanup()
    try:
        await _stage([_sheet([1, 2, 3])])
        await _file_it()
        await _stage([_sheet([1, 2, 3]), _sheet([8, 9], name="week2.pdf")])      # a new week arrived
        t = await _item()
        assert t.status == PipelineStatus.NEEDS_REVIEW and t.failure_code == FailureCode.PENDING_REVIEW
        assert t.record_id == "record-1"                                         # the filed record is kept
        assert "after this month was filed" in (t.failure_detail or "")
    finally:
        await _cleanup()


async def test_an_incomplete_re_read_never_counts_as_nothing_changed():
    await _cleanup()
    try:
        await _stage([_sheet([1, 2, 3])])
        await _file_it()
        await _stage([_sheet([1, 2, 3])], incomplete=True, errors=["Pass 2: 'x.pdf' could not be read"])
        t = await _item()
        assert t.status == PipelineStatus.NEEDS_REVIEW                           # flagged and retried
        assert t.extraction_meta["full_email_extract"]["incomplete"] is True
    finally:
        await _cleanup()


async def test_an_item_still_awaiting_review_is_refreshed_as_before():
    await _cleanup()
    try:
        await _stage([_sheet([1, 2, 3])])
        await _stage([_sheet([1, 2, 3, 4])])
        t = await _item()
        assert t.status == PipelineStatus.NEEDS_REVIEW
        assert len(t.extraction_meta["staged"]["working_days"]) == 4
    finally:
        await _cleanup()
