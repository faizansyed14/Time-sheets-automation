"""Stage pipeline review items."""
from __future__ import annotations

import calendar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStage, PipelineStatus
from app.services.extract_email.constants import BUCKETS, TAG_PREFIX
from app.services.extract_email.results import now, now_iso

_LEAVE_LABEL = {
    "annual": "annual", "remote": "WFH", "sick": "sick", "maternity": "maternity",
    "unpaid": "unpaid", "absent": "absent", "public_holiday": "public holiday",
    "other": "other leave",
}


def sheet_summaries(sheets: list[dict]) -> list[dict]:
    """The per-sheet shape shown in extraction_meta.full_email_extract.sheets —
    what the Pipeline page's "what was extracted" panel reads. One place so a
    retry (ingestion.py) and the original stage (below) can never drift apart."""
    return [{
        "filename": s["name"], "kind": s["kind"],
        "employee_name": s["employee_name"], "employee_id": s["employee_id"],
        "month": s["month"], "year": s["year"],
        "manager_signature": s.get("manager_signature", False),
        # WHAT was detected, not just whether — every path that sets
        # manager_signature also computes this (roster's _row_approval,
        # thread_extract's signature_evidence), but until now it was
        # computed and then dropped here, so Compare & Fix could only ever
        # show a bare true/false with no explanation of what was actually seen.
        "approval_evidence": s.get("approval_evidence", ""),
        "approval_named_only": bool(s.get("approval_named_only")),
        "leave_days": sum(len(s.get(b) or []) for b in BUCKETS),
        "working_days": len(s.get("working_days") or []),
        "weekend_days": len(s.get("weekend_days") or []),
        "uncertain_days": len(s.get("uncertain_days") or []),
        "days_covered": s.get("days_covered", 0),
        "period_type": s.get("period_type", "unknown"),
        "missing_days": s.get("missing_days") or [],
        "unaccounted_days": s.get("_unaccounted_days") or [],
    } for s in sheets]


def summarize_group(g: dict) -> str:
    """A clean, readable one-paragraph summary of a month's extraction —
    always available (no LLM needed)."""
    month, year = g.get("month"), g.get("year")
    if not (month and year):
        return "Could not read a month/year — pick the period in Compare & Fix."
    buckets = g.get("buckets") or {}
    parts = [f"{len(buckets.get(b) or [])} {label}" for b, label in _LEAVE_LABEL.items() if buckets.get(b)]
    leave_total = sum(len(v or []) for v in buckets.values())
    head = f"{calendar.month_name[month]} {year} — " + (", ".join(parts) if parts else "no leave recorded")
    n_sheets = len(g.get("sheets") or [])
    head += f" ({leave_total} leave day{'s' if leave_total != 1 else ''}"
    head += f", {n_sheets} files)." if n_sheets > 1 else ")."
    working, weekend = len(g.get("working_days") or []), len(g.get("weekend_days") or [])
    if working or weekend:
        head += f" {working} worked, {weekend} weekend."
    n_flags = len(g.get("issues") or []) + len(g.get("uncertain_days") or []) + len(g.get("unaccounted_days") or [])
    if not n_flags:
        return head + " No issues found — clean and ready for approval."
    return head + f" {n_flags} issue{'s' if n_flags != 1 else ''} need review."


def group_flags(g: dict) -> list[str]:
    flags = list(dict.fromkeys(g.get("overlap_flags", []) + g.get("fold_notes", []) + g.get("issues", [])))
    if not (g.get("month") and g.get("year")):
        flags.append("No usable month/year on these sheets — pick the period.")
    for s in g.get("sheets") or []:
        miss = [d for d in (s.get("missing_days") or [])
                if d in (g.get("missing_days") or [])]    # not covered by another sheet
        if miss and s.get("kind") == "timesheet":
            shown = ", ".join(str(d) for d in miss[:8]) + (" …" if len(miss) > 8 else "")
            flags.append(f'Sheet "{s.get("name")}" has {len(miss)} missing day(s): {shown}')
        unc = s.get("uncertain_days") or []
        if unc:
            flags.append(f'Sheet "{s.get("name")}" has {len(unc)} uncertain day(s).')
    return list(dict.fromkeys(flags))


def _alternates_for(g: dict, alternates: list[dict]) -> list[dict]:
    """The alternate-copy records whose kept sheet belongs to this group."""
    names = {s.get("name") for s in (g.get("sheets") or [])}
    return [a for a in alternates if a.get("kept") in names]


def _alternate_note(a: dict) -> str:
    stored = ", ".join(a.get("stored_only") or [])
    diff = (a.get("differences") or "").strip().rstrip(".")
    return (f'Also received {stored} - the same timesheet as "{a.get("kept")}"'
            + (f" ({diff})" if diff else "")
            + f'. It is stored with this item; only "{a.get("kept")}" was read '
            f'({a.get("how_chosen")}).')


def group_approval(g: dict, approval: dict, n_groups: int) -> dict:
    """The approval verdict for ONE employee+month group.

    The thread-wide `approval` is derived across every sheet of the email. With
    one group that is exactly right. With several (one email, several
    employees' timesheets) a signature found on employee A's sheet must NOT
    approve employee B's:
      * a sheet in THIS group carries a real signature/stamp -> approved, with
        that sheet's own evidence;
      * the thread-wide approval is conversation-level (a reply / separate
        approval screenshot) -> inherited, with a note when it may not be
        specific to this employee;
      * the thread-wide approval came from another group's sheet -> NOT
        inherited."""
    own = [s for s in (g.get("sheets") or [])
           if s.get("manager_signature") and not s.get("approval_named_only")]
    if own:
        ev = next((s.get("approval_evidence") for s in own if s.get("approval_evidence")), "")
        return {**approval, "detected": True, "where": "sheet",
                "evidence": ev or approval.get("evidence", ""),
                "source": own[0].get("name"),
                "detail": f"Approval found: {ev[:200]}" if ev else "Approval found on this employee's sheet."}
    if n_groups <= 1 or not approval.get("detected"):
        return approval
    if approval.get("where") == "conversation":
        return {**approval, "detail": approval.get("detail", "Approval found.")
                + " (conversation-level - confirm it covers this employee)"}
    return {**approval, "detected": False, "where": "none", "evidence": "", "source": "",
            "detail": "No manager approval found for THIS employee - the approval in this email "
                      "is on another employee's sheet."}


def _signature(buckets: dict, working, weekend, uncertain, sheet_names) -> tuple:
    """What a group's extraction says, in a comparable shape: the leave days per
    category, worked / weekend / uncertain days, and which files it came from."""
    def days(v):
        return tuple(sorted(str(d.get("date") if isinstance(d, dict) else d) for d in (v or [])))
    return (
        tuple(sorted((b, days(v)) for b, v in (buckets or {}).items() if v)),
        days(working), days(weekend), days(uncertain),
        tuple(sorted(str(n) for n in (sheet_names or []))),
    )


def _signature_of_group(g: dict) -> tuple:
    return _signature(g.get("buckets"), g.get("working_days"), g.get("weekend_days"),
                      g.get("uncertain_days"), [s.get("name") for s in (g.get("sheets") or [])])


def _approval_gained(t: PipelineFile, g_approval: dict) -> bool:
    """A manager's approval is on record now that was not when this item was staged -
    new information even though the days and sheets are identical."""
    old = ((t.extraction_meta or {}).get("full_email_extract") or {}).get("approval") or {}
    return bool(g_approval.get("detected")) and not old.get("detected")


def _signature_of_item(t: PipelineFile) -> tuple | None:
    meta = t.extraction_meta or {}
    st = meta.get("staged")
    if not isinstance(st, dict):
        return None
    fe = meta.get("full_email_extract") or {}
    return _signature(st.get("buckets"), st.get("working_days"), st.get("weekend_days"),
                      st.get("uncertain_days"), [x.get("filename") for x in (fe.get("sheets") or [])])


async def stage_groups(
    db: AsyncSession, *, source_kind: str, source_id: str,
    raw_bytes: bytes, raw_name: str, content_type: str,
    groups: list[dict], approval: dict, run_meta: dict,
    thread_key: str | None = None, keep_existing_raw: bool = False,
) -> list[PipelineFile]:
    """One item per employee+month group — the SINGLE staging path for every
    entry point (Extract Email, Upload, chat store).

    Each group is scored by the AI auto-accept engine (auto_accept.evaluate).
    When every check passes the item is still staged NEEDS_REVIEW with an "AI
    recommends accept" flag — nothing is filed until a human presses Accept in
    Review. Groups with blockers are staged the same way but without the
    recommendation."""
    from app.services.extract_email import auto_accept
    from app.services.extract_email.progress import emit
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

    # ---- run-level health --------------------------------------------------
    # An INCOMPLETE run (a model call failed, a message would not download)
    # never silently looks like a clean one: every item it stages carries the
    # problem as a flag + auto-accept blocker, it counts attempts so the retry
    # logic is bounded, and it must not delete previously staged items (the
    # missing piece may be exactly what those items came from).
    incomplete = bool(run_meta.get("incomplete"))
    run_flags: list[str] = []
    if incomplete:
        errs = [str(e) for e in (run_meta.get("errors") or [])]
        run_flags.append("EXTRACTION INCOMPLETE - " + "; ".join(errs[:3])
                         + (" …" if len(errs) > 3 else "")
                         + " - it will be retried; verify before accepting.")
    run_flags.extend(str(w) for w in (run_meta.get("warnings") or []))
    prior_attempts = 0
    for t0 in existing:
        prev = (t0.extraction_meta or {}).get("full_email_extract") or {}
        if prev.get("incomplete"):
            prior_attempts = max(prior_attempts, int(prev.get("incomplete_attempts") or 1))
    run_meta = {**run_meta, "incomplete_attempts": (prior_attempts + 1) if incomplete else 0}

    staged: list[PipelineFile] = []
    used: set[str] = set()
    for g in groups:
        month, year = g["month"], g["year"]
        g_approval = group_approval(g, approval, len(groups))
        group_alts = _alternates_for(g, run_meta.get("alternates") or [])
        flags = list(dict.fromkeys(group_flags(g) + run_flags + [_alternate_note(a) for a in group_alts]))
        summary = f"{summarize_group(g)} {g_approval['detail']}"

        decision = auto_accept.evaluate(g, extra_flags=run_flags, approval=g_approval)

        display = raw_name if len(groups) == 1 else \
            f"{g['name'] or 'Unassigned sheets'} — {raw_name}"
        tag = g["tag"]
        used.add(tag)
        t = by_tag.get(tag)
        # An item that is already FILED (accepted -> a record exists) and whose thread
        # was just re-read in full with exactly the same result: a later "Thanks!" /
        # "ok" reply changed nothing for this month. Leave it filed - do not bounce it
        # back into the review queue - and only move its watermark so the thread is
        # not re-read again and again. (An incomplete run proves nothing; it takes the
        # normal path so it is flagged and retried.)
        if (t is not None and t.record_id and not incomplete
                and t.status in (PipelineStatus.SUCCESS, PipelineStatus.RESOLVED)
                and _signature_of_item(t) == _signature_of_group(g)
                and not _approval_gained(t, g_approval)):
            meta = dict(t.extraction_meta or {})
            fe = dict(meta.get("full_email_extract") or {})
            fe["snapshot_at"] = run_meta.get("snapshot_at") or fe.get("snapshot_at")
            fe["rechecked_at"] = now_iso()
            meta["full_email_extract"] = fe
            t.extraction_meta = meta
            t.events = (t.events or []) + [{
                "stage": PipelineStage.EXTRACTION, "status": "ok",
                "detail": "Thread re-read after a new message: nothing new for this month, "
                          "it stays filed.",
                "at": now_iso()}]
            staged.append(t)
            continue
        was_filed = bool(t is not None and t.record_id)
        approval_gained = bool(t is not None and _approval_gained(t, g_approval))
        if t is None:
            t = PipelineFile(
                filename=display, content_type=content_type,
                size_bytes=len(raw_bytes), source_kind=source_kind,
                source_id=source_id, thread_key=thread_key, attachment_id=tag)
            try:
                # SAVEPOINT: if another writer inserted this very item between
                # our lookup and now (the partial unique index in migration 0034
                # refuses the duplicate), only this insert is rolled back - the
                # groups already prepared in this run are kept - and we adopt
                # the winner's row instead of creating a second one.
                async with db.begin_nested():
                    db.add(t)
                    await db.flush()
            except IntegrityError:
                winner = (await db.execute(select(PipelineFile).where(
                    PipelineFile.source_kind == source_kind,
                    PipelineFile.thread_key == thread_key,
                    PipelineFile.attachment_id == tag))).scalars().first()
                if winner is None:
                    raise
                t = winner
                existing = list(existing) + [winner]
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
        if not (keep_existing_raw and t.raw_path):
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
                "buckets": g["buckets"],
                "working_days": g.get("working_days") or [],
                "weekend_days": g.get("weekend_days") or [],
                "uncertain_days": g.get("uncertain_days") or [],
                "unaccounted_days": g.get("unaccounted_days") or [],
                "validation_status": "manual_review" if flags else "verified",
                "flags": flags,
                "summary": summary,
                "extraction_status": "ok",
            },
            "auto_accept": decision.as_meta(),
            "full_email_extract": {
                **run_meta,
                "match_note": g["note"],
                "approval": g_approval,
                "sheets": sheet_summaries(g["sheets"]),
                # How many employees' groups this SAME run produced — e.g. a
                # manager sending 8 people's sheets in one email. Read at
                # Accept time (ingestion.py's _isolate_employee_attachments)
                # to decide whether it's safe to file just this employee's
                # own attachment(s) instead of the whole shared thread.
                "group_count": len(groups),
                # Copies of this same timesheet that were stored with the item but
                # not read separately (identical entries; e.g. the unsigned copy of
                # a signed sheet). Kept so the files travel with the item.
                "alternates": group_alts,
            },
            "source_kind": source_kind,
        }
        total = sum(len(v) for v in g["buckets"].values())
        t.events = (t.events or []) + [{
            "stage": PipelineStage.EXTRACTION, "status": "ok",
            "detail": (f"Full-email extraction: {len(g['sheets'])} sheet(s) → {total} leave day(s)"
                       f" for {g['name'] or 'unassigned'}. {g_approval['detail']}"
                       + (" INCOMPLETE - will be retried." if incomplete else "")),
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

        if was_filed:
            t.failure_detail = (
                ("A manager's approval arrived after this month was filed - accept again to "
                 "record it. " if approval_gained else
                 "New data arrived after this month was filed - review it and accept to merge "
                 "it into the record. ") + (t.failure_detail or ""))
        t.status = PipelineStatus.NEEDS_REVIEW
        t.failure_code = FailureCode.PENDING_REVIEW
        staged.append(t)

    # A re-run that produced different groups must not leave stale items behind.
    # An item that already FILED a record is never dropped, though: deleting it
    # would erase the audit trail for a record that still exists.
    for t in existing:
        if t.record_id:
            continue
        if incomplete or run_meta.get("partial_read"):
            break           # a partial read can't prove an old item is stale
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
