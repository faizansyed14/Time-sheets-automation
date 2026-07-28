"""The agent line-up for the (single) thread pipeline.

Extract Email and Upload both run the SAME two-pass vision read over the
whole conversation/submission (ThreadAgent), then a deterministic tail:
resolve identity, report multi-sheet consolidation, check duplicates, decide
auto-accept vs review, then file.

There is no per-sheet fallback pipeline and no separate Validation/Approval
agent — cross-bucket/date validation now happens inside
grouping.normalise_sheet() (called from EmployeeAgent's group_sheets), and
approval is read straight off pass 1's own per-item verdicts (see
thread_extract._derive_approval), so a second, less-informed detector would
only second-guess it.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

from app.services.orchestrator.base import Agent, AgentContext, AgentInfo


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 1. Thread Agent — the whole conversation, two focused passes
# --------------------------------------------------------------------------- #
class ThreadAgent(Agent):
    """Extract Email's (and Upload's) reader: two focused passes over the
    whole conversation/submission.

    Pass 1 classifies every item — which are really timesheets, whose each
    one is, whether a manager approved, and what the conversation is about.
    Pass 2 reads only the items pass 1 confirmed, transcribing a full
    day-by-day account (working/weekend/leave/uncertain), not just a scan
    for leave.
    """

    info = AgentInfo("thread", "Thread Agent",
                     "reading the whole conversation, then extracting the sheets it confirms",
                     uses_llm=True)

    async def run(self, ctx: AgentContext) -> str:
        from sqlalchemy import select

        from app.models.month_calendar import MonthCalendar
        from app.services.extract_email import sheet_cache
        from app.services.extract_email.thread_extract import extract_thread_sheets
        from app.services.extract_email.thread_summary import load_summary, save_summary
        from app.services.extraction import vision_client

        messages = ctx.thread_messages or [(ctx.raw_name, ctx.raw_bytes)]
        model = vision_client.model_for(vision_client.vision_provider(), "vision")

        # Admin-configured (month, year) calendars (see /admin/calendars) —
        # handed to Pass 2 as ground truth for weekends/public holidays
        # instead of the model inferring them. Typically a handful of rows,
        # so fetching all of them is cheap; which ones apply isn't known
        # until Pass 1 gives each item a period_hint (see thread_prompt's
        # _parse_period_hint), so this can't be a targeted per-month query.
        calendar_rows = (await ctx.db.execute(select(MonthCalendar))).scalars().all()
        calendars = {
            (r.month, r.year): {"weekend_weekdays": r.weekend_weekdays or [],
                                "public_holidays": r.public_holidays or []}
            for r in calendar_rows
        }

        # Incremental by default: an already-extracted thread only sends the
        # new message(s) + a couple of context messages to the model (see
        # email.py's windowing via collect_thread_emls' `since`). Whatever was
        # already extracted from everything else is reused here from the
        # per-attachment cache — never silently dropped, never re-sent either.
        # `force_full` (the "Re-read entire thread" action) bypasses this
        # entirely and re-reads everything fresh, exactly like before this
        # existed — the fix for a suspected-stale read.
        conversation_id = getattr(ctx.source, "conversation_id", None)
        msg_id = getattr(ctx.source, "provider_message_id", None)
        cached = {}
        if not ctx.force_full and (conversation_id or msg_id):
            cached = await sheet_cache.thread_cached_sheets(
                ctx.db, conversation_id, [msg_id] if msg_id else None)

        # Full debug trace of this run (raw prompts/responses per pass-1/
        # pass-2 call, dropped-item images) — a temporary testing aid, see
        # /admin/debug. Captured unconditionally for now; best-effort only,
        # a debug-write failure must never break real extraction.
        from app.services.extract_email import debug_capture
        cap = debug_capture.DebugCapture()
        cap_token = debug_capture.set_capture(cap)
        try:
            sheets, approval, conflicts, meta = await extract_thread_sheets(
                messages, cached_sheets=cached, calendars=calendars)
        finally:
            debug_capture.reset_capture(cap_token)
        try:
            from app.models.extraction_debug_run import ExtractionDebugRun
            ctx.db.add(ExtractionDebugRun(
                id=cap.run_id, source_kind=ctx.source_kind, source_id=ctx.source_id,
                thread_key=ctx.thread_key, subject=getattr(ctx.source, "subject", None),
                model=model, calls=meta.get("calls", 0), reused_sheets=meta.get("reused_sheets", 0),
                pass1_calls=cap.pass1_calls, pass2_calls=cap.pass2_calls,
                dropped_items=cap.dropped_items, triage=meta.get("triage") or [],
                sheets=sheets, errors=meta.get("errors") or [],
            ))
            await ctx.db.commit()
        except Exception:
            await ctx.db.rollback()

        # A signature/approval found in an EARLIER run — possibly on a message
        # outside this run's window — must never be forgotten just because
        # this run didn't re-read that message. Merge with whatever this run
        # found, keeping whichever is more specific (sheet > conversation >
        # none); ties go to this run since it's the fresher read.
        if not ctx.force_full:
            prior_summary = await load_summary(
                ctx.db, conversation_id, [msg_id] if msg_id else [])
            prior_approval = (prior_summary or {}).get("approval")
            if prior_approval and prior_approval.get("detected"):
                rank = {"sheet": 2, "conversation": 1, "none": 0}
                if rank.get(prior_approval.get("where"), 0) > rank.get(approval.get("where"), 0):
                    approval = dict(prior_approval)

        # Record WHICH attachments were freshly read (not to reuse — to badge
        # them, and so the next run's cache lookup finds them).
        fresh = meta.pop("_fresh_by_digest", None) or {}
        if fresh:
            await sheet_cache.remember(ctx.db, ctx.source_id, model, fresh)

        # Pass 1's conversation summary IS the thread summary — there is no
        # separate summarisation call any more. The at-a-glance facts are
        # DERIVED from what pass 1 already reported rather than asked for
        # again: the model saying "a timesheet was sent" while its own items
        # list is empty is a contradiction we would then have to adjudicate.
        summary_obj = meta.get("summary_obj") or meta.get("thread_summary")
        if summary_obj and summary_obj.get("headline"):
            # Prefer the already-enriched summary from extract_thread_sheets;
            # only fill gaps if an older path left fields missing.
            thread_summary = {
                "timesheet_sent": False,
                "approval_requested": False,
                "approval_given": bool(approval.get("detected")),
                "employee": "",
                "period": "",
                "message_count": len(messages),
                "model": model,
                "at": _now_iso(),
                **summary_obj,
            }
            if "timesheet_sent" not in summary_obj or "employee" not in summary_obj:
                triage = meta.get("triage") or []
                data_items = [t for t in triage
                              if t.get("kind") in ("timesheet", "leave_certificate")]
                employees = [t.get("employee_name") for t in data_items if t.get("employee_name")]
                periods = [t.get("period_hint") for t in data_items if t.get("period_hint")]
                thread_summary.update({
                    "timesheet_sent": bool(data_items),
                    "approval_requested": summary_obj.get("status") == "awaiting_approval",
                    "approval_given": bool(approval.get("detected")),
                    "employee": ", ".join(dict.fromkeys(employees)),
                    "period": next(iter(dict.fromkeys(periods)), ""),
                    "message_count": len(messages),
                    "model": model,
                    "at": _now_iso(),
                })
            # Carried forward by a later run's cache-aware read (see above) —
            # without this, an approval found on a message that later falls
            # outside the incremental window would be lost after one more reply.
            thread_summary["approval"] = approval
            meta["thread_summary"] = thread_summary
            try:
                await save_summary(ctx.db, ctx.source_id, thread_summary)
            except Exception:
                pass    # a summary that fails to store must not fail the run

        ctx.sheets, ctx.approval, ctx.run_meta = sheets, approval, meta
        ctx.conflicts = conflicts
        if meta.get("summary"):
            ctx.notes.append(str(meta["summary"]))
        skipped = meta.get("skipped") or []
        if skipped:
            names = [s.get("name") for s in skipped if isinstance(s, dict) and s.get("name")]
            if names:
                ctx.notes.append("Not sent (size/noise filter): " + ", ".join(names[:4]))
        if not sheets:
            ctx.abort("no readable sheets")
            return meta.get("errors", ["nothing readable in this thread"])[0]

        kinds: dict[str, int] = {}
        for s in sheets:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        breakdown = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        return (f"read {len(sheets)} sheet(s) from {len(messages)} message(s) "
                f"in {meta.get('calls', 0)} call(s) — {breakdown}")


# --------------------------------------------------------------------------- #
# 2. Employee Resolution Agent — identity → HR master (deterministic + fuzzy)
# --------------------------------------------------------------------------- #
class EmployeeAgent(Agent):
    info = AgentInfo("employee", "Employee Resolution Agent",
                     "matching each sheet to the HR master record, normalising every date")

    def skip_reason(self, ctx: AgentContext) -> str | None:
        return None if ctx.sheets else "no sheets"

    async def run(self, ctx: AgentContext) -> str:
        from app.services.extract_email.grouping import group_sheets

        ctx.groups = await group_sheets(ctx.db, ctx.source, ctx.sheets)
        if not ctx.groups:
            ctx.abort("no timesheet or certificate found")
            return "nothing identifiable to file"
        matched = [g for g in ctx.groups if g.get("employee_pk")]
        names = ", ".join(g["name"] for g in ctx.groups if g.get("name"))
        return (f"{len(ctx.groups)} record(s), {len(matched)} matched to HR"
                + (f" — {names}" if names else ""))


# --------------------------------------------------------------------------- #
# 3. Conversation Agent — merge partial periods into one monthly record
# --------------------------------------------------------------------------- #
class ConversationAgent(Agent):
    """Reports the partial-period consolidation for each month.

    IMPORTANT — this agent does NOT re-merge anything. `group_sheets` already
    keys every group by (employee, month, year), so weekly / half-month sheets
    (1–15 + 16–30) for the same employee+month are ALREADY unioned into one
    group before this runs (verified). Re-merging here would be dead code.
    What was missing is visibility: this surfaces which sheets were consolidated
    into each month so the reviewer can see a month was built from partials.
    """

    info = AgentInfo("conversation", "Conversation Agent",
                     "consolidating multi-part sheets into one record per month")

    def skip_reason(self, ctx: AgentContext) -> str | None:
        return None if ctx.groups else "no records"

    async def run(self, ctx: AgentContext) -> str:
        multi = [g for g in ctx.groups if len(g.get("sheets") or []) > 1]
        if not multi:
            return "one sheet per record — nothing to consolidate"
        details = []
        for g in multi:
            names = ", ".join(s["name"] for s in g["sheets"])
            month = g.get("month")
            period = f"{calendar.month_name[month]} {g.get('year')}" if month else "the period"
            msg = (f"{g.get('name') or 'Record'} — {period} built from "
                   f"{len(g['sheets'])} sheets ({names}); their leave is unioned.")
            details.append(msg)
            g.setdefault("fold_notes", []).append(msg)
        ctx.notes.extend(details)
        return "; ".join(details[:2]) + (f" (+{len(details) - 2} more)" if len(details) > 2 else "")


# --------------------------------------------------------------------------- #
# 4. Duplicate Agent — same sheet twice, or a month already filed
# --------------------------------------------------------------------------- #
class DuplicateAgent(Agent):
    info = AgentInfo("duplicate", "Duplicate Agent",
                     "checking for repeat submissions and already-filed months")

    def skip_reason(self, ctx: AgentContext) -> str | None:
        return None if ctx.groups else "no records"

    async def run(self, ctx: AgentContext) -> str:
        from sqlalchemy import select

        from app.models.timesheet_record import TimesheetRecord

        # NOTE: byte-identical attachments are NOT checked here — the .eml
        # collector already content-hash de-duplicates them, so a duplicate
        # can never reach this point. What IS new is spotting a month that
        # was already filed, so the reviewer knows this will MERGE.
        found: list[str] = []
        for g in ctx.groups:
            pk, month, year = g.get("employee_pk"), g.get("month"), g.get("year")
            if not (pk and month and year):
                continue
            existing = (await ctx.db.execute(select(TimesheetRecord).where(
                TimesheetRecord.matched_employee_pk == pk,
                TimesheetRecord.month == month,
                TimesheetRecord.year == year))).scalars().first()
            if existing is not None:
                msg = (f"{g.get('name') or 'This employee'} already has a filed "
                       f"{calendar.month_name[month]} {year} record — these sheets will "
                       "MERGE into it (existing leave is kept).")
                found.append(msg)
                g.setdefault("fold_notes", []).append(msg)

        if not found:
            return "no duplicates found"
        ctx.notes.extend(found)
        return "; ".join(found[:2]) + (f" (+{len(found) - 2} more)" if len(found) > 2 else "")


# --------------------------------------------------------------------------- #
# 5. Decision Agent — auto-accept / review, then stage + file
# --------------------------------------------------------------------------- #
class DecisionAgent(Agent):
    info = AgentInfo("decision", "Decision Agent",
                     "deciding auto-accept vs human review, then filing")

    def skip_reason(self, ctx: AgentContext) -> str | None:
        return None if ctx.groups else "nothing to stage"

    async def run(self, ctx: AgentContext) -> str:
        from app.services.extract_email.staging import stage_groups

        ctx.staged = await stage_groups(
            ctx.db, source_kind=ctx.source_kind, source_id=ctx.source_id,
            thread_key=ctx.thread_key,
            raw_bytes=ctx.raw_bytes, raw_name=ctx.raw_name,
            content_type=ctx.content_type, groups=ctx.groups,
            approval=ctx.approval or {"detected": False, "detail": ""},
            run_meta=ctx.run_meta)
        filed = sum(
            1 for t in ctx.staged
            if (t.extraction_meta or {}).get("auto_accept", {}).get("accepted"))
        held = len(ctx.staged) - filed
        parts = []
        if filed:
            parts.append(f"{filed} AI recommends accept")
        if held:
            parts.append(f"{held} held for review")
        return ", ".join(parts) or "nothing staged"


def build_thread_pipeline(*, stage: bool = True) -> list[Agent]:
    """The ONE pipeline: read the whole conversation/submission (ThreadAgent),
    then the deterministic tail (identity, consolidation, duplicates,
    auto-accept)."""
    agents: list[Agent] = [
        ThreadAgent(),
        EmployeeAgent(), ConversationAgent(), DuplicateAgent(),
    ]
    if stage:
        agents.append(DecisionAgent())
    return agents
