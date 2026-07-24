"""Stage pipeline review items."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStage, PipelineStatus
from app.services.extract_email.constants import TAG_PREFIX
from app.services.extract_email.results import now, now_iso

async def stage_groups(
    db: AsyncSession, *, source_kind: str, source_id: str,
    raw_bytes: bytes, raw_name: str, content_type: str,
    groups: list[dict], approval: dict, run_meta: dict,
) -> list[PipelineFile]:
    """One item per employee+month group — the SINGLE staging path for every
    entry point (Extract Email, selected attachments, Upload, chat store).

    Each group is scored by the AI auto-accept engine. A HIGH-confidence,
    fully-verified group is FILED immediately (record + vault) and its item is
    marked SUCCESS = "Auto-accepted by AI". Anything short is staged
    NEEDS_REVIEW for Compare & Fix, with the blocker reasons recorded."""
    from app.services.extract_email import auto_accept
    from app.services.extract_email.progress import emit
    from app.services.extraction.validation import summarize as summarize_record
    from app.services.extraction.validation import validate
    from app.services.pipeline import raw_store
    from app.services.pipeline.ingestion import (
        _file_key, ingest_manual_entry, mark_source_email_ingested,
    )

    existing = (await db.execute(select(PipelineFile).where(
        PipelineFile.source_kind == source_kind,
        PipelineFile.source_id == source_id,
        PipelineFile.attachment_id.like(f"{TAG_PREFIX}%"),
        PipelineFile.failure_code == FailureCode.PENDING_REVIEW,
    ))).scalars().all()
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
                source_id=source_id, attachment_id=tag)
            db.add(t)
            await db.flush()
        t.filename = display
        if not t.raw_path:
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
                "sheets": [{
                    "filename": s["name"], "kind": s["kind"],
                    "employee_name": s["employee_name"], "employee_id": s["employee_id"],
                    "month": s["month"], "year": s["year"],
                    "manager_signature": s["manager_signature"],
                    "leave_days": sum(len(v) for v in s["buckets"].values()),
                    "format_id": s.get("format_id"),
                    "incomplete_sheet": bool(s.get("incomplete_sheet")),
                    "dates_complete": s.get("dates_complete", True),
                    "missing_days": s.get("missing_days") or [],
                    "classify_confidence": s.get("classify_confidence"),
                } for s in g["sheets"]],
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

        filed = False
        if decision.accepted and g.get("employee_pk"):
            # File the record automatically — same path as a human Accept.
            try:
                rec_approval = None
                if approval.get("detected"):
                    rec_approval = {"approved": True, "detail": approval.get("detail", "")}
                rec, tmp_tracker = await ingest_manual_entry(
                    db, employee_pk=g["employee_pk"], month=month, year=year,
                    buckets=cleaned, approval=rec_approval,
                    # Carry the source (.eml / uploaded sheet) through so the
                    # File Vault gets the evidence — exactly what a human Accept
                    # does. Without this the record is filed pointing at an
                    # EMPTY vault folder and the raw copy is then purged.
                    attachments=[(raw_name, content_type, raw_bytes)],
                    note=f"Auto-accepted by AI — {'; '.join(decision.reasons[:4])}.",
                    source_key=_file_key(source_id, tag, display),
                    source_filename=(g["name"] or display))
                await db.delete(tmp_tracker)   # ingest made its own tracker; keep ours
                t.record_id = rec.id
                t.status = PipelineStatus.SUCCESS
                t.failure_code = None
                t.failure_detail = None
                t.resolved_at = now()
                t.resolution_note = "Auto-accepted by AI (high confidence)."
                t.events.append({
                    "stage": PipelineStage.RECORDED, "status": "ok",
                    "detail": "Auto-accepted by AI: " + "; ".join(decision.reasons[:5]),
                    "at": now_iso(),
                })
                raw_store.delete_raw(t.raw_path)
                t.raw_path = None
                await mark_source_email_ingested(db, t)
                filed = True
                emit("autoaccept", "ok",
                     f"{g['name'] or 'Record'} — AUTO-ACCEPTED by AI and filed.",
                     employee=g["name"], reasons=decision.reasons)
                emit("file", "ok",
                     f"Filed {g['name'] or 'record'} — {month}/{year} ({total} leave day(s)).")
            except Exception as exc:
                # Never lose a group to an auto-file error — fall back to review.
                decision.accepted = False
                decision.blockers.append(f"auto-file failed: {str(exc)[:120]}")
                t.extraction_meta["auto_accept"] = decision.as_meta()

        if not filed:
            emit("autoaccept", "warn",
                 f"{g['name'] or 'Record'} — held for review: "
                 + ("; ".join(decision.blockers[:2]) if decision.blockers else "needs a human check"),
                 employee=g["name"], blockers=decision.blockers)
            t.status = PipelineStatus.NEEDS_REVIEW
            t.failure_code = FailureCode.PENDING_REVIEW
            hold = (" AI held for review: " + "; ".join(decision.blockers[:3])
                    if decision.blockers else "")
            t.failure_detail = ("Extracted from the full email — review the leaves and accept to file."
                                + (f" ({g['name']})" if g["name"] and len(groups) > 1 else "")
                                + hold)
        staged.append(t)

    # A re-run that produced different groups must not leave stale items behind.
    for t in existing:
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
