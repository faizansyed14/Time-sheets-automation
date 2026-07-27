"""The agent line-up.

Each agent wraps ONE responsibility. Where solid, tested logic already exists
(unpacking, vision extraction, approval, grouping, auto-accept) the agent calls
it rather than reimplementing — the agents give that pipeline structure,
observability and clean seams, not a rewrite.

What is genuinely NEW here (audited against the existing code so nothing is
duplicated):
  * DuplicateAgent    — warns when a month is ALREADY filed, so the reviewer
    knows the sheets will merge into it. (Byte-identical attachments are NOT
    re-checked: the .eml collector and merge_thread_units already content-hash
    de-duplicate upstream.)
  * ConversationAgent — reports which sheets were consolidated into each month.
    (It does NOT re-merge: group_sheets already keys by employee+month+year, so
    1–15 + 16–30 / weekly partials arrive already unioned — verified by test.)
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

from app.services.orchestrator.base import Agent, AgentContext, AgentInfo


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 1. Email Agent — unpack the message, keep the hierarchy
# --------------------------------------------------------------------------- #
class EmailAgent(Agent):
    info = AgentInfo("email", "Email Agent",
                     "unpacking the message, attachments and nested emails")

    async def run(self, ctx: AgentContext) -> str:
        from app.services.extract_email.collector import collect_units, merge_thread_units
        from app.services.extract_email.upload import units_from_upload

        if ctx.source_kind == "upload":
            # A plain uploaded file (PDF/DOCX/XLSX) or an uploaded .eml — this
            # helper handles both and gives the context its subject/body.
            ctx.source, ctx.units = units_from_upload(ctx.raw_name, ctx.raw_bytes)
        else:
            ctx.units = collect_units(ctx.source, ctx.raw_bytes)
        if ctx.prior_source is not None:
            from app.services.email_provider import get_email_provider
            from app.services.inbox.eml_export import build_full_eml
            prior_bytes, _ = await build_full_eml(get_email_provider(), ctx.prior_source)
            prior_units = collect_units(ctx.prior_source, prior_bytes)
            before = len(ctx.units)
            ctx.units = merge_thread_units(ctx.units, prior_units)
            if len(ctx.units) > before:
                ctx.notes.append(
                    f"Pulled {len(ctx.units) - before} sheet(s) from the previous "
                    "message in this conversation.")
        if not ctx.units:
            ctx.abort("no readable sheets")
            return "no readable sheets found"
        return f"{len(ctx.units)} sheet(s): " + ", ".join(u.name for u in ctx.units[:4])


# --------------------------------------------------------------------------- #
# 2. Attachment Agent — route each document by type (deterministic)
# --------------------------------------------------------------------------- #
class AttachmentAgent(Agent):
    info = AgentInfo("attachment", "Attachment Agent",
                     "routing PDFs, Excel, Word and images, and detecting the client template")

    async def run(self, ctx: AgentContext) -> str:
        from app.services.extract_email.formats import get_format
        from app.services.extraction import vision_client

        native = images = text_only = 0
        for u in ctx.units:
            if u.ftype in vision_client.NATIVE_FILE_TYPES:
                native += 1
            elif u.images:
                images += 1
            else:
                text_only += 1
        templates = sorted({get_format(u.format_id).label
                            for u in ctx.units if u.format_id != "generic"})
        bits = []
        if native:
            bits.append(f"{native} sent as native file(s)")
        if images:
            bits.append(f"{images} as image(s)")
        if text_only:
            bits.append(f"{text_only} as text")
        if templates:
            bits.append("template: " + ", ".join(templates))
        return "; ".join(bits) or "nothing to route"


# --------------------------------------------------------------------------- #
# 3. Vision Agent — the LLM read (batched)
# --------------------------------------------------------------------------- #
class VisionAgent(Agent):
    info = AgentInfo("vision", "OCR / Vision Agent",
                     "reading every sheet with the vision model", uses_llm=True)

    async def run(self, ctx: AgentContext) -> str:
        from app.services.extract_email.analyser import analyse_units

        ctx.sheets, ctx.run_meta = await analyse_units(ctx.source, ctx.units)
        kinds: dict[str, int] = {}
        for s in ctx.sheets:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        breakdown = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        # `calls` is the two-stage (classify + extract) counter; `batches` is
        # the older single-stage name — support both.
        calls = ctx.run_meta.get("calls", ctx.run_meta.get("batches", 0))
        return f"read {len(ctx.sheets)} sheet(s) in {calls} extraction call(s) — {breakdown}"


# --------------------------------------------------------------------------- #
# Thread Agent — the WHOLE conversation in ONE model call
# --------------------------------------------------------------------------- #
class ThreadAgent(Agent):
    """Extract Email's reader: two focused passes over the whole conversation.

    Pass 1 understands the thread — which items are really timesheets, whose
    each one is, whether a manager approved, and what the conversation is
    about. Pass 2 reads only the sheets pass 1 validated, and does nothing but
    transcribe leave.

    Everything goes up: bodies, attachments, images (logos included), and
    emails attached inside emails. The model decides what a pasted approval
    screenshot is, rather than a size threshold deciding for it.
    """

    info = AgentInfo("thread", "Thread Agent",
                     "reading the whole conversation, then extracting the sheets it confirms",
                     uses_llm=True)

    async def run(self, ctx: AgentContext) -> str:
        from app.services.extract_email import sheet_cache
        from app.services.extract_email.thread_extract import extract_thread_sheets
        from app.services.extract_email.thread_summary import save_summary
        from app.services.extraction import vision_client

        messages = ctx.thread_messages or [(ctx.raw_name, ctx.raw_bytes)]
        model = vision_client.model_for(vision_client.vision_provider(), "vision")

        # Every run re-reads every attachment. Reusing a previous result made
        # a bad read permanent: a sheet marked "LEAVE (MEDICAL)" was booked as
        # ANNUAL leave, and re-extracting served the same wrong answer back
        # because the file had not changed. Fixing the prompt has to be enough
        # to fix the data, so nothing is reused.
        #
        # What IS still recorded (below) is WHICH attachments have been read —
        # that drives the Extracted/New badges, and reading it back is not the
        # same as trusting it.
        sheets, approval, conflicts, meta = await extract_thread_sheets(messages)

        # Record WHICH attachments were read (not to reuse — to badge them).
        fresh = meta.pop("_fresh_by_digest", None) or {}
        if fresh:
            await sheet_cache.remember(ctx.db, ctx.source_id, model, fresh)

        # Pass 1's conversation summary IS the thread summary — there is no
        # separate summarisation call any more. The at-a-glance facts are
        # DERIVED from what pass 1 already reported rather than asked for
        # again: the model saying "a timesheet was sent" while its own items
        # list is empty is a contradiction we would then have to adjudicate.
        summary_obj = meta.get("summary_obj")
        if summary_obj and summary_obj.get("headline"):
            triage = meta.get("triage") or []
            data_items = [t for t in triage
                          if t.get("kind") in ("timesheet", "leave_certificate")]
            employees = [t.get("employee_name") for t in data_items if t.get("employee_name")]
            periods = [t.get("period_hint") for t in data_items if t.get("period_hint")]
            thread_summary = {
                **summary_obj,
                "timesheet_sent": bool(data_items),
                "approval_requested": summary_obj.get("status") == "awaiting_approval",
                "approval_given": bool(approval.get("detected")),
                "employee": ", ".join(dict.fromkeys(employees)),
                "period": next(iter(dict.fromkeys(periods)), ""),
                "message_count": len(messages),
                "model": model,
                "at": _now_iso(),
            }
            # On run_meta (not just saved to the EmailMessage row) so the
            # Pipeline page's extraction_meta.full_email_extract — which
            # spreads run_meta wholesale in staging.py — carries the SAME
            # plain-English summary the Inbox thread view shows, with no
            # second place computing it and no second query to fetch it.
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
        over_capacity = [n for n, reason in skipped if reason == "over_capacity"]
        unsupported = [n for n, reason in skipped if reason == "unsupported_type"]
        if over_capacity:
            # A real, readable file that simply didn't fit under the cap —
            # this is a capacity problem, not a filetype problem, and must not
            # be mislabelled as one or the actual issue (raise MAX_FILES /
            # split the thread) never gets noticed.
            ctx.notes.append(
                f"NOT sent — this thread has more attachments than one call "
                f"handles ({len(over_capacity)} skipped): " + ", ".join(over_capacity[:4]))
        if unsupported:
            ctx.notes.append("Not sent (unsupported type): " + ", ".join(unsupported[:4]))
        if not sheets:
            ctx.abort("no readable sheets")
            return meta.get("errors", ["nothing readable in this thread"])[0]

        kinds: dict[str, int] = {}
        for s in sheets:
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        breakdown = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        return (f"read {len(sheets)} sheet(s) from {len(messages)} message(s) "
                f"in 2 calls — {breakdown}")


# --------------------------------------------------------------------------- #
# 4. Approval Agent — manager sign-off across the whole conversation
# --------------------------------------------------------------------------- #
class ApprovalAgent(Agent):
    info = AgentInfo("approval", "Approval Agent",
                     "searching the conversation, attachments and screenshots for a manager approval")

    def skip_reason(self, ctx: AgentContext) -> str | None:
        # The thread call already reports approval with quoted evidence from
        # the whole conversation; re-running the text detector would only
        # second-guess it with less context.
        if ctx.approval and ctx.approval.get("evidence") is not None:
            return "already answered by the thread call"
        return None

    async def run(self, ctx: AgentContext) -> str:
        from app.services.extract_email.approval import detect_approval

        used_vision = str(ctx.run_meta.get("method", "")).startswith("vision")
        ctx.approval = detect_approval(ctx.source, ctx.sheets, used_vision=used_vision)
        return ctx.approval["detail"]


# --------------------------------------------------------------------------- #
# 5. Employee Resolution Agent — identity → HR master (deterministic + fuzzy)
# --------------------------------------------------------------------------- #
class EmployeeAgent(Agent):
    info = AgentInfo("employee", "Employee Resolution Agent",
                     "matching each sheet to the HR master record")

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
# 6. Conversation Agent — merge partial periods into one monthly record
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
# 7. Duplicate Agent — same sheet twice, or a month already filed
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
        # collector already content-hash de-duplicates them (and
        # merge_thread_units does the same across thread messages), so a
        # duplicate can never reach this point. What IS new is spotting a month
        # that was already filed, so the reviewer knows this will MERGE.
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

        # Conflicts the thread call reported ACROSS sheets — most importantly
        # two sheets both claiming the same full month, which must never be
        # filed unreviewed. Recorded as an overlap flag so auto-accept blocks.
        for c in ctx.conflicts or []:
            ctype = str(c.get("type") or "other")
            if ctype == "partial_merge":
                continue                      # complementary halves — fine, they merge
            detail = str(c.get("detail") or "").strip()
            names = ", ".join(str(n) for n in (c.get("sheets") or [])[:4])
            who = str(c.get("employee") or "").strip().lower()
            msg = (f"{'Two sheets claim the SAME full month' if ctype == 'duplicate_full_month' else 'Duplicate'}"
                   f"{f' ({names})' if names else ''}"
                   f"{f' — {detail}' if detail else ''}")
            for g in ctx.groups:
                gname = (g.get("name") or "").strip().lower()
                gid = (g.get("employee_id") or "").strip().lower()
                if not who or who in (gname, gid) or who in gname:
                    if c.get("month") in (None, g.get("month")):
                        g.setdefault("overlap_flags", []).append(msg)
            found.append(msg)

        if not found:
            return "no duplicates found"
        ctx.notes.extend(found)
        return "; ".join(found[:2]) + (f" (+{len(found) - 2} more)" if len(found) > 2 else "")


# --------------------------------------------------------------------------- #
# 8. Validation Agent — business rules (deterministic)
# --------------------------------------------------------------------------- #
class ValidationAgent(Agent):
    info = AgentInfo("validation", "Validation Agent",
                     "checking dates, duplicates within the month and calendar limits")

    def skip_reason(self, ctx: AgentContext) -> str | None:
        return None if ctx.groups else "no records"

    async def run(self, ctx: AgentContext) -> str:
        from app.services.extraction.validation import validate

        total_flags = 0
        for g in ctx.groups:
            month, year = g.get("month"), g.get("year")
            if not (month and year):
                g["validation_flags"] = ["No usable month/year on these sheets."]
                total_flags += 1
                continue
            cleaned, flags = validate(g["buckets"], month, year)
            g["buckets"] = cleaned
            g["validation_flags"] = flags
            total_flags += len(flags)
        return ("all records passed validation" if not total_flags
                else f"{total_flags} flag(s) raised for review")


# --------------------------------------------------------------------------- #
# 9. Decision Agent — auto-accept / review, then stage + file
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


def build_pipeline(*, stage: bool = True) -> list[Agent]:
    """Per-sheet line-up — used by Upload and Manual entry, where there is one
    file (or a handful) and no conversation to read.

    `stage=False` stops before the Decision Agent — used by previews that
    analyse a sheet without staging or filing anything."""
    agents: list[Agent] = [
        EmailAgent(), AttachmentAgent(), VisionAgent(), ApprovalAgent(),
        EmployeeAgent(), ConversationAgent(), DuplicateAgent(), ValidationAgent(),
    ]
    if stage:
        agents.append(DecisionAgent())
    return agents


def build_thread_pipeline(*, stage: bool = True) -> list[Agent]:
    """Extract Email's line-up: ONE model call for the whole conversation,
    then the same deterministic tail (identity, consolidation, duplicates,
    validation, auto-accept) that Upload uses."""
    # No ApprovalAgent: pass 1 already reports approval with quoted evidence
    # from the whole conversation, so the text detector always skipped — it
    # only added a permanently-greyed row to the live view.
    agents: list[Agent] = [
        ThreadAgent(),
        EmployeeAgent(), ConversationAgent(), DuplicateAgent(), ValidationAgent(),
    ]
    if stage:
        agents.append(DecisionAgent())
    return agents
