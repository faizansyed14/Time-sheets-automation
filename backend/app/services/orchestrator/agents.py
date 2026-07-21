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

from app.services.orchestrator.base import Agent, AgentContext, AgentInfo


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
# 4. Approval Agent — manager sign-off across the whole conversation
# --------------------------------------------------------------------------- #
class ApprovalAgent(Agent):
    info = AgentInfo("approval", "Approval Agent",
                     "searching the conversation, attachments and screenshots for a manager approval")

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
            raw_bytes=ctx.raw_bytes, raw_name=ctx.raw_name,
            content_type=ctx.content_type, groups=ctx.groups,
            approval=ctx.approval or {"detected": False, "detail": ""},
            run_meta=ctx.run_meta)
        filed = sum(1 for t in ctx.staged if getattr(t, "status", "") == "success")
        held = len(ctx.staged) - filed
        parts = []
        if filed:
            parts.append(f"{filed} auto-accepted and filed")
        if held:
            parts.append(f"{held} held for review")
        return ", ".join(parts) or "nothing staged"


def build_pipeline(*, stage: bool = True) -> list[Agent]:
    """The ordered agent line-up used by every extraction entry point.

    `stage=False` stops before the Decision Agent — used by the chat preview,
    which analyses a sheet without staging or filing anything."""
    agents: list[Agent] = [
        EmailAgent(), AttachmentAgent(), VisionAgent(), ApprovalAgent(),
        EmployeeAgent(), ConversationAgent(), DuplicateAgent(), ValidationAgent(),
    ]
    if stage:
        agents.append(DecisionAgent())
    return agents
