"""Stage pipeline review items."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStage, PipelineStatus
from app.services.extract_email.constants import TAG_PREFIX
from app.services.extract_email.results import now, now_iso


def sheet_summaries(sheets: list[dict]) -> list[dict]:
    """The per-sheet shape shown in extraction_meta.full_email_extract.sheets —
    what the Pipeline page's "what was extracted" panel reads. One place so a
    retry (ingestion.py) and the original stage (below) can never drift apart."""
    return [{
        "filename": s["name"], "kind": s["kind"],
        "employee_name": s["employee_name"], "employee_id": s["employee_id"],
        "month": s["month"], "year": s["year"],
        "manager_signature": s.get("manager_signature", False),
        "leave_days": sum(len(v) for v in s["buckets"].values()),
        "format_id": s.get("format_id"),
        "incomplete_sheet": bool(s.get("incomplete_sheet")),
        "dates_complete": s.get("dates_complete", True),
        "missing_days": s.get("missing_days") or [],
        "classify_confidence": s.get("classify_confidence"),
    } for s in sheets]


async def stage_groups(
    db: AsyncSession, *, source_kind: str, source_id: str,
    raw_bytes: bytes, raw_name: str, content_type: str,
    groups: list[dict], approval: dict, run_meta: dict,
    thread_key: str | None = None,
) -> list[PipelineFile]:
    """One item per employee+month group — the SINGLE staging path for every
    entry point (Extract Email, selected attachments, Upload, chat store).

    Each group is scored by the AI auto-accept engine. When every check passes
    the item is still staged NEEDS_REVIEW with an "AI recommends accept" flag —
    nothing is filed until a human presses Accept in Review. Groups with
    blockers are staged the same way but without the recommendation."""
    from app.services.extract_email import auto_accept
    from app.services.extract_email.progress import emit
    from app.services.extraction.validation import summarize as summarize_record
    from app.services.extraction.validation import validate
    from app.services.pipeline import raw_store

    # Match on the CONVERSATION when we have one. Extract Email reads a whole
    # thread, so a reply arriving later must re-run into the SAME review items
    # — matching on source_id (one message) produced a second set for the same
    # employee+month, plus another stored copy of the thread.
    scope = (PipelineFile.thread_key == thread_key if thread_key
             else PipelineFile.source_id == source_id)
    where = [
        PipelineFile.source_kind == source_kind,
        scope,
        PipelineFile.attachment_id.like(f"{TAG_PREFIX}%"),
    ]
    if not thread_key:
        # Legacy per-message runs only ever reused items still awaiting review.
        where.append(PipelineFile.failure_code == FailureCode.PENDING_REVIEW)
    # With a thread key we reuse the item WHATEVER its state — including one
    # already auto-accepted. Restricting to PENDING_REVIEW meant a filed thread
    # grew a second row on every later reply, so the pipeline showed the same
    # employee+month again and again.
    existing = (await db.execute(select(PipelineFile).where(*where))).scalars().all()
    by_tag = {t.attachment_id: t for t in existing}

    staged: list[PipelineFile] = []
    used: set[str] = set()
    for g in groups:
        month, year = g["month"], g["year"]
        if month and year:
            cleaned, val_flags = validate(g["buckets"], month, year)
            summary = summarize_record(cleaned, val_flags, month, year, len(g["sheets"]))
        else:
            cleaned, val_flags = g["buckets"], ["No usable month/year on these sheets — pick the period."]
            summary = "Could not read a month/year — pick the period in Compare & Fix."
        flags = list(dict.fromkeys(g["overlap_flags"] + g["fold_notes"] + val_flags))
        for s in g.get("sheets") or []:
            if s.get("incomplete_sheet") or (
                    s.get("kind") == "timesheet" and s.get("dates_complete") is False):
                miss = s.get("missing_days") or []
                miss_txt = (", ".join(str(d) for d in miss[:8]) if miss else "unknown days")
                flags.append(
                    f'Sheet "{s.get("name")}" incomplete day coverage '
                    f'({s.get("observed_day_count", 0)}/{s.get("expected_day_count", 0)}; '
                    f'missing: {miss_txt})')
        flags = list(dict.fromkeys(flags))
        summary = f"{summary} {approval['detail']}"

        # AI auto-accept decision (uses the group's OWN buckets for coverage;
        # validation flags block it).
        g_for_eval = {**g, "buckets": cleaned}
        decision = auto_accept.evaluate(g_for_eval, extra_flags=val_flags)

        display = raw_name if len(groups) == 1 else \
            f"{g['name'] or 'Unassigned sheets'} — {raw_name}"
        tag = g["tag"]
        used.add(tag)
        t = by_tag.get(tag)
        if t is None:
            t = PipelineFile(
                filename=display, content_type=content_type,
                size_bytes=len(raw_bytes), source_kind=source_kind,
                source_id=source_id, thread_key=thread_key, attachment_id=tag)
            db.add(t)
            await db.flush()
        t.filename = display
        # Point at the message that triggered THIS run, so a retry re-fetches
        # the newest message rather than the one first seen weeks ago.
        t.source_id = source_id
        t.thread_key = thread_key or t.thread_key
        t.size_bytes = len(raw_bytes)
        # Replace the stored evidence: a re-run after a new reply carries a
        # LONGER thread, and the item must show what was actually read. Written
        # under the same key, so a thread stores one copy however often it is
        # re-extracted.
        if t.raw_path:
            raw_store.delete_raw(t.raw_path)
        t.raw_path = raw_store.save_raw(t.id, raw_name, raw_bytes)
        t.employee_id = g["employee_id"]
        t.employee_name = g["name"]
        t.month, t.year = month, year
        t.extraction_method = run_meta["method"]
        t.extraction_model = run_meta["model"]
        t.extraction_meta = {
            "staged": {
                "employee_pk": g["employee_pk"],
                "matched_name": g["name"],
                "matched_employee_id": g["employee_id"],
                "month": month, "year": year,
                "buckets": cleaned,
                "validation_status": "manual_review" if flags else "verified",
                "flags": flags,
                "summary": summary,
                "extraction_status": "ok",
            },
            "auto_accept": decision.as_meta(),
            "full_email_extract": {
                **run_meta,
                "match_note": g["note"],
                "approval": approval,
                "sheets": sheet_summaries(g["sheets"]),
            },
            "source_kind": source_kind,
        }
        total = sum(len(v) for v in cleaned.values())
        t.events = (t.events or []) + [{
            "stage": PipelineStage.EXTRACTION, "status": "ok",
            "detail": (f"Full-email extraction: {len(g['sheets'])} sheet(s) → {total} leave day(s)"
                       f" for {g['name'] or 'unassigned'}. {approval['detail']}"),
            "at": now_iso(),
        }]

        if decision.accepted:
            emit("autoaccept", "ok",
                 f"{g['name'] or 'Record'} — AI recommends accept.",
                 employee=g["name"], reasons=decision.reasons)
            t.failure_detail = (
                "AI recommends accepting — review the leaves and press Accept to file."
                + (f" ({g['name']})" if g["name"] and len(groups) > 1 else "")
            )
        else:
            emit("autoaccept", "warn",
                 f"{g['name'] or 'Record'} — held for review: "
                 + ("; ".join(decision.blockers[:2]) if decision.blockers else "needs a human check"),
                 employee=g["name"], blockers=decision.blockers)
            hold = (" AI held for review: " + "; ".join(decision.blockers[:3])
                    if decision.blockers else "")
            t.failure_detail = ("Extracted from the full email — review the leaves and accept to file."
                                + (f" ({g['name']})" if g["name"] and len(groups) > 1 else "")
                                + hold)

        t.status = PipelineStatus.NEEDS_REVIEW
        t.failure_code = FailureCode.PENDING_REVIEW
        staged.append(t)

    # A re-run that produced different groups must not leave stale items behind.
    # An item that already FILED a record is never dropped, though: deleting it
    # would erase the audit trail for a record that still exists.
    for t in existing:
        if t.record_id:
            continue
        if t.attachment_id not in used:
            raw_store.delete_raw(t.raw_path)
            await db.delete(t)

    await db.commit()
    for t in staged:
        await db.refresh(t)
    return staged


async def mark_no_sheets(db: AsyncSession, email: EmailMessage, note: str) -> None:
    """Persist "Extract Email ran, found nothing to stage" on the email row
    itself so the UI can show a lasting badge/filter instead of the user
    having to re-click Extract Email to rediscover the same empty result."""
    email.no_sheets_found_at = now()
    email.no_sheets_note = note[:500]
    await db.commit()
