"""
Full-email extraction — the SIMPLE path. One button, the whole email in,
review items out. THE VISION MODEL DOES THE UNDERSTANDING — there is no
heuristic pre-filtering deciding what gets analysed.

  email ──► full .eml (native MIME — every attachment, forwarded emails and
            THEIR attachments, nothing missing)
        ──► EVERY attachment inside + the email body is rendered to page
            images (plus the file's own text, when it has one, as grounding)
        ──► vision model, up to 2 sheets per call (batched, fewer when a
            provider's per-prompt image cap requires it — a heavy sheet
            never gets truncated, it just gets its own call), ONE clean
            prompt: per sheet → kind, identity as written, period, every
            leave category, manager signature on the sheet, approval evidence
        ──► per-sheet results grouped by EMPLOYEE + MONTH
        ──► one pending-review pipeline item PER GROUP, whose raw copy is the
            full .eml — so Compare & Fix shows the whole email and all its
            attachments on the right and the extracted data on the left.

Edge cases handled explicitly:

- ONE employee, many sheets (attendance sheet + sick-leave certificates):
  everything folds into ONE review item; buckets are UNIONED; a date claimed
  by two sheets in the same category is flagged, never double counted.
- MANY employees in one email (a manager forwards a batch): sheets are grouped
  by matched identity — one review item PER employee. A sheet whose identity
  cannot be read is NOT guessed into a group when several employees are
  present; it becomes its own flagged item for the reviewer.
- Same employee, TWO formats (our ADR sheet + the client-site sheet) for the
  same month: same group → ONE item, union of buckets + conflict flags.
  Different months → one item per month.
- Certificates without a name/ID: when the email resolves to exactly one
  employee they fold into that employee's item (with a note); with multiple
  employees they stay separate so a human assigns them.
- Approval: read by the MODEL — manager signatures on sheets, approval
  screenshots as their own kind, approval wording in the body. (A small
  pattern check exists ONLY for the keyless fallback below.)
- Logos / signature images / cover notes: sent to the model like everything
  else — it classifies them "other" and they simply don't stage.
- No API key / vision failure: every sheet falls back to the standard
  per-file extraction engine (deterministic/mock in dev), so the button
  always works and costs $0 without a key.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_message import EmailMessage
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStage, PipelineStatus

_BATCH_SIZE = 2            # sheets per vision call (batching requirement)
_MAX_PAGES_PER_SHEET = 4   # page images sent per sheet
_MAX_SHEETS = 12           # hard cap of sheets analysed per email
_TAG_PREFIX = "__email_extract__"

_BUCKETS = ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")

# Used ONLY by the keyless fallback (`used_vision=False`) — with an API key
# the model reads approvals; nothing is pattern-matched.
_NEG_APPROVAL_RE = re.compile(r"\b(not\s+approved|un-?approved|disapproved|reject(?:ed)?)\b", re.I)
# A REQUEST / ask to approve is NOT an approval — these veto a positive match so
# "please approve", "need your approval", "for your approval" don't count as approved.
_REQ_APPROVAL_RE = re.compile(
    r"\b(please|kindly|pls|need(?:s|ed)?|require[ds]?|request(?:ing)?|await(?:ing)?|"
    r"pending|seeking|for\s+your|to\s+be)\b[^.\n]{0,30}\bapprov(?:e|al|ing)\b", re.I)
# Future / passive-pending phrasing that ends in past-tense "approved" but still
# means NOT-yet-approved: "to be approved", "yet to be approved", "awaiting approved".
_REQ2_APPROVAL_RE = re.compile(
    r"\b((?:yet\s+)?to\s+be|awaiting|pending|needs?\s+to\s+be)\s+approved\b", re.I)
# Only GRANTED wording (past-tense "approved", not the bare verb "approve").
_POS_APPROVAL_RE = re.compile(
    r"\b(approved|approval\s+(?:granted|given|confirmed)|ok(?:ay)?\s+to\s+process|"
    r"looks\s+good|lgtm|sign(?:ed)?\s*[- ]?off)\b", re.I)

_SYSTEM_PROMPT = """You read documents from ONE email sent to an HR timesheet portal.
You receive one or more sheets as page images, and — when a file has one — its exact extracted text.

For EVERY sheet, independently, report:

kind — exactly one of:
  timesheet          a day-by-day attendance / hours / leave grid, in any format, signed or not
  leave_certificate  a medical or leave certificate/letter covering specific days
  approval           a manager approving a timesheet or leave (email/chat screenshot or note)
  other              anything else: cover notes, logos, signatures, banners, invoices, audit pages

employee_name, employee_id — EXACTLY as printed on that sheet; null when not printed.
  Never copy identity from another sheet or from the email address.

month, year — the period printed on the sheet (or clearly implied by its dates); null when absent.

Leave dates, ISO YYYY-MM-DD, each date in exactly ONE list:
  annual           annual / paid leave
  remote           work from home / remote work
  sick             sick leave (for a leave_certificate, put the certified days here
                   unless it clearly states another type)
  maternity        maternity leave (not annual or sick)
  unpaid           unpaid leave / LOP
  absent           absence without leave
  public_holiday   public holidays marked on the sheet
  Normal worked days and weekends are NOT leave. An empty grid means empty lists.
  MERGED / SPANNING MARKS: one label, merged cell, bracket or colored block that
  covers SEVERAL date rows applies to EVERY date it covers (e.g. "Eid Al Adha"
  written once across 25–29 = five public_holiday dates). Count the rows the
  mark spans — do not record only the first row.
  COLOR-CODED SHEETS: when leave is marked by cell colour/highlight, find the
  legend, map each colour to its leave type, and mark EVERY date whose row or
  cell carries that colour. Text annotations like [fill=...] describe cell
  colours — match them against the legend the same way.

manager_signature — true only when THAT sheet visibly carries a manager/supervisor
  signature, stamp, or signed approval block.

approval_evidence — fill this ONLY when a manager has ALREADY approved the
  timesheet/leave: a signed/stamped approval block, or clearly GRANTED wording such
  as "Approved", "Approval granted", "Timesheet approved", or "please find the
  approved timesheet". Quote the exact granting words; otherwise "".
  A REQUEST, ask, or intention to approve is NOT approval — leave it "" for phrasing
  like "please approve", "kindly approve", "need/awaiting your approval", "for your
  approval", "pending approval", "to be approved", "please review and approve".
  A rejection ("not approved", "rejected") is NOT approval — leave it "".
  When in doubt, treat it as NOT approved and leave it "".

Special case — the sheet named "(email body)" is the message text itself:
  it IS a timesheet when a day-by-day attendance grid is pasted anywhere in the text —
  including inside a quoted or forwarded email lower in the thread (very common: the
  employee pastes the grid with IN/OUT hours, the manager replies "Approved" on top —
  read the grid rows for identity, period and leave; rows marked Holiday/Leave/Sick
  are leave dates, worked days and weekend rows are not). It is "other" ONLY when the
  text merely refers to an ATTACHED file and contains no grid itself. Record wording
  in approval_evidence ONLY if it GRANTS approval (e.g. the manager replies "Approved"
  on top of the thread); an employee ASKING for approval ("please approve", "need your
  approval") is NOT approval and must leave approval_evidence "".

Never invent values — when unsure, use null / empty. Reply with ONLY the requested JSON."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


@dataclass
class SheetUnit:
    """One analysable sheet found inside the full .eml."""
    name: str
    ftype: str
    payload: bytes
    images: list[bytes] = field(default_factory=list)
    text: str = ""


# --------------------------------------------------------------------------- #
# 1) Collect every sheet inside the full .eml
# --------------------------------------------------------------------------- #
def _collect_units(email: EmailMessage, eml_bytes: bytes) -> list[SheetUnit]:
    """EVERY document inside the .eml (attachments, forwarded emails and their
    attachments) plus the email body — no heuristic filtering; the vision
    model decides what each sheet is."""
    from app.services.extraction import ocr
    from app.services.extraction.file_processor import (
        email_body_to_images, eml_all_attachments, extract_document_text, to_images,
    )

    units: list[SheetUnit] = []
    for name, payload, ftype in eml_all_attachments(eml_bytes)[:_MAX_SHEETS]:
        try:
            images = to_images(ftype, payload)[:_MAX_PAGES_PER_SHEET]
        except Exception:
            images = []
        text = ""
        try:
            text = extract_document_text(ftype, payload) or ""
        except Exception:
            pass
        if not text.strip() and images and ocr.ocr_status() == "ready":
            try:
                text = ocr.ocr_text(images, payload, ftype) or ""
            except Exception:
                pass
        if images or text.strip():
            units.append(SheetUnit(name or "attachment", ftype, payload, images, text.strip()))

    body = (email.body_text or "").strip()
    if body:
        try:
            imgs = email_body_to_images(email.subject, email.body_text)
        except Exception:
            imgs = []
        if imgs:
            units.append(SheetUnit("(email body)", "image", imgs[0],
                                   imgs[:_MAX_PAGES_PER_SHEET], body[:12000]))
    return units


# --------------------------------------------------------------------------- #
# 2) Analyse — vision model in batches of _BATCH_SIZE, engine fallback per sheet
# --------------------------------------------------------------------------- #
def _clean_dates(vals, month, year) -> list[str]:
    """Normalise model dates to ISO. ISO-first (the prompt asks for it — the
    shared DMY parser mis-reads YYYY-MM-DD), then DMY wording, then bare day
    numbers resolved against the sheet's own month/year."""
    import datetime as dt
    from app.services.extraction.parser import _parse_one_leave_date
    out = set()
    for v in vals or []:
        s = str(v).strip()
        if not s:
            continue
        d = None
        try:
            d = dt.date.fromisoformat(s[:10])
        except ValueError:
            d = _parse_one_leave_date(s, month, year)
            if d is None and s.isdigit() and month and year:
                try:
                    d = dt.date(year, month, int(s))
                except ValueError:
                    d = None
        if d:
            out.add(d.isoformat())
    return sorted(out)


def _as_month(v) -> int | None:
    """1-12 from an int, a numeric string, or a month name/abbr ("May", "Sep").
    Models differ: some return month as an integer, others echo the sheet's
    wording — both must land on the same value."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 1 <= v <= 12 else None
    s = str(v or "").strip().lower()
    if not s:
        return None
    if s.isdigit():
        i = int(s)
        return i if 1 <= i <= 12 else None
    import calendar
    for i in range(1, 13):
        if s == calendar.month_name[i].lower() or s[:3] == calendar.month_abbr[i].lower():
            return i
    return None


def _as_year(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 2000 else None
    s = str(v or "").strip()
    return int(s) if s.isdigit() and int(s) >= 2000 else None


def _month_token(tok: str) -> int | None:
    import calendar
    s = (tok or "").strip().lower()
    if not s:
        return None
    if s.isdigit():
        m = int(s)
        return m if 1 <= m <= 12 else None
    for i in range(1, 13):
        if s == calendar.month_name[i].lower() or s[:3] == calendar.month_abbr[i].lower():
            return i
    return None


# Filename/subject hints when the vision model returns kind=other (common when
# the configured model cannot read images or returns empty JSON).
_TIMESHEET_FNAME_RE = re.compile(
    r"(?i)timesheet[_\s-]+(?P<month>[a-z]{3,9}|\d{1,2})[_\s-]+"
    r"(?P<year>20\d{2})(?:[_\s-]+(?P<tail>[^.]+))?"
)
_EMP_ID_IN_TEXT = re.compile(r"(?i)\b(E\d{3,})\b")
_LEAVE_CERT_FNAME_RE = re.compile(
    r"(?i)\b(sick[\s_-]*leave|medical[\s_-]*(cert|certificate)|"
    r"leave[\s_-]*cert(?:ificate)?)\b"
)
_SUBJECT_TS_RE = re.compile(
    r"(?i)timesheet\s+for\s+(?P<month>[a-z]+|\d{1,2})\s+(?P<year>20\d{2})"
    r"\s*\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<id>E\d+)"
)


def _infer_from_filename(filename: str, subject: str | None = None) -> dict:
    """Best-effort kind / identity / period from attachment name (+ subject)."""
    name = (filename or "").strip()
    if not name or name == "(email body)":
        return {}
    low = name.lower()
    out: dict = {}

    m = _TIMESHEET_FNAME_RE.search(name)
    if m:
        out["kind"] = "timesheet"
        if month := _month_token(m.group("month")):
            out["month"] = month
        if year := _as_year(m.group("year")):
            out["year"] = year
        tail = (m.group("tail") or "").strip()
        if tail:
            if id_m := _EMP_ID_IN_TEXT.search(tail):
                out["employee_id"] = id_m.group(1).upper()
                name_part = tail[:id_m.start()].strip(" _-")
                if name_part and not _EMP_ID_IN_TEXT.fullmatch(name_part):
                    out["employee_name"] = name_part.replace("_", " ").title()
    elif _LEAVE_CERT_FNAME_RE.search(low):
        out["kind"] = "leave_certificate"
        for i in range(1, 13):
            import calendar
            for label in (calendar.month_name[i].lower(), calendar.month_abbr[i].lower()):
                if len(label) >= 3 and label in low:
                    out["month"] = i
                    break
            if out.get("month"):
                break
    elif "timesheet" in low:
        out["kind"] = "timesheet"
        if id_m := _EMP_ID_IN_TEXT.search(name):
            out["employee_id"] = id_m.group(1).upper()

    subj = subject or ""
    if subj_m := _SUBJECT_TS_RE.search(subj):
        out.setdefault("kind", "timesheet")
        out.setdefault("month", _month_token(subj_m.group("month")))
        out.setdefault("year", _as_year(subj_m.group("year")))
        out.setdefault("employee_name", subj_m.group("name").strip())
        out.setdefault("employee_id", subj_m.group("id").upper())

    return {k: v for k, v in out.items() if v is not None}


def _boost_sheet_from_hints(sheet: dict, unit: SheetUnit, subject: str | None) -> dict:
    hints = _infer_from_filename(unit.name, subject)
    if not hints:
        return sheet
    out = dict(sheet)
    if out.get("kind") == "other" and hints.get("kind"):
        out["kind"] = hints["kind"]
    for key in ("employee_name", "employee_id", "month", "year"):
        if not out.get(key) and hints.get(key):
            out[key] = hints[key]
    return out


def _normalize_sheet(unit: SheetUnit, raw: dict) -> dict:
    kind = str(raw.get("kind") or "other").lower()
    if kind not in ("timesheet", "leave_certificate", "approval", "other"):
        kind = "other"
    month = _as_month(raw.get("month"))
    year = _as_year(raw.get("year"))
    return {
        "name": unit.name,
        "kind": kind,
        "employee_name": (str(raw.get("employee_name")).strip() or None)
        if raw.get("employee_name") else None,
        "employee_id": (str(raw.get("employee_id")).strip() or None)
        if raw.get("employee_id") else None,
        "month": month,
        "year": year,
        "buckets": {b: _clean_dates(raw.get(b), month, year) for b in _BUCKETS},
        "manager_signature": bool(raw.get("manager_signature")),
        "approval_evidence": str(raw.get("approval_evidence") or "")[:200],
    }


async def _engine_sheet(unit: SheetUnit) -> dict:
    """Fallback: run one sheet through the standard extraction engine
    (deterministic/mock without a key — the button always works)."""
    from app.services.extraction import get_extraction_engine
    try:
        ext = await get_extraction_engine().extract_timesheet(
            unit.payload, unit.name, "", "full-email", unit.name)
    except Exception:
        return _normalize_sheet(unit, {"kind": "other"})
    buckets = {
        "annual": ext.annual_leave_dates or [], "remote": ext.remote_work_dates or [],
        "sick": ext.sick_leave_dates or [], "maternity": ext.maternity_leave_dates or [],
        "unpaid": ext.unpaid_leave_dates or [],
        "absent": ext.absent_dates or [], "public_holiday": ext.public_holiday_dates or [],
    }
    has_period = bool(1 <= (ext.month or 0) <= 12 and (ext.year or 0) >= 2000)
    has_data = any(buckets.values()) or has_period or ext.employee_id or ext.employee_name
    return _normalize_sheet(unit, {
        "kind": "timesheet" if has_data else "other",
        "employee_name": ext.employee_name, "employee_id": ext.employee_id,
        "month": ext.month if has_period else None,
        "year": ext.year if has_period else None,
        **buckets,
    })


def _batch_prompt(email: EmailMessage, batch: list[SheetUnit]) -> str:
    # No sender line: the prompt forbids using the address as identity, and
    # sender matching runs locally after the model call — so it never needs to
    # reach the provider at all. The subject stays (it often names the employee
    # and period); any address/phone in it is scrubbed at the client boundary.
    lines = [
        f"EMAIL SUBJECT: {email.subject or ''}",
        "",
        f"This batch contains {len(batch)} sheet(s), as page images in order:",
    ]
    img_no = 1
    for i, u in enumerate(batch, 1):
        n = len(u.images)
        if n == 0:
            lines.append(f'  SHEET {i} "{u.name}" -> no image; read its exact text below')
            continue
        rng = f"image {img_no}" if n == 1 else f"images {img_no}-{img_no + n - 1}"
        lines.append(f'  SHEET {i} "{u.name}" -> {rng}')
        img_no += n
    lines.append(
        "\nAnalyse every sheet and reply with ONLY this JSON object "
        "(one entry per sheet, in the same order):\n"
        "{\n"
        '  "sheets": [\n'
        "    {\n"
        '      "index": 1,\n'
        '      "kind": "timesheet" | "leave_certificate" | "approval" | "other",\n'
        '      "employee_name": "<exactly as printed>" | null,\n'
        '      "employee_id": "<exactly as printed>" | null,\n'
        '      "month": 1-12 | null,\n'
        '      "year": <int> | null,\n'
        '      "annual": ["YYYY-MM-DD", ...],\n'
        '      "remote": [], "sick": [], "maternity": [], "unpaid": [], "absent": [], "public_holiday": [],\n'
        '      "manager_signature": true | false,\n'
        '      "approval_evidence": "<exact GRANTED-approval quote; \\"\\" for a request to approve>" | ""\n'
        "    }\n"
        "  ]\n"
        "}")
    for i, u in enumerate(batch, 1):
        if u.text:
            lines.append(
                f'\n--- EXACT TEXT OF SHEET {i} "{u.name}" (extracted from the file; '
                "trust it over the image for names, IDs and dates) ---\n" + u.text[:8000])
    return "\n".join(lines)


def _make_batches(units: list[SheetUnit], max_per_batch: int, max_images: int | None) -> list[list[int]]:
    """Group sheet indices into vision calls: up to `max_per_batch` sheets per
    call, and never more than `max_images` total page images in one call (a
    hosted vLLM server can reject the whole call above its per-prompt cap —
    see VLLM_MAX_IMAGES_PER_PROMPT). A sheet is NEVER split or truncated to
    fit; a heavy sheet just gets its own call, so nothing is lost for
    accuracy — it only means fewer sheets share that particular call."""
    batches: list[list[int]] = []
    current: list[int] = []
    current_images = 0
    for i, u in enumerate(units):
        n = len(u.images or [])
        over_count = len(current) >= max_per_batch
        over_images = max_images is not None and current_images + n > max_images
        if current and (over_count or over_images):
            batches.append(current)
            current, current_images = [], 0
        current.append(i)
        current_images += n
    if current:
        batches.append(current)
    return batches


async def _analyse_units(email: EmailMessage, units: list[SheetUnit]) -> tuple[list[dict], dict]:
    """Vision batches first; any sheet the batch could not cover falls back to
    the per-file engine. Returns (sheets, run_meta)."""
    from app.services.extraction import vision_client

    provider = vision_client.vision_provider()
    model = vision_client.model_for(provider, "vision")
    _, _, api_key, _, _ = vision_client._chat_endpoint(provider)
    use_vision = bool(api_key) and api_key.lower() != "change-me"
    sheets: list[dict | None] = [None] * len(units)
    batches = 0
    errors: list[str] = []

    if use_vision:
        from app.services.extraction.parser import extract_json_from_vllm_response
        # OpenAI has no meaningful per-prompt image cap for this batch size;
        # vLLM (and other self-hosted OpenAI-compatible servers) do.
        max_images = None if provider == "openai" else settings.vllm_max_images_per_prompt
        for batch_no, idx_group in enumerate(_make_batches(units, _BATCH_SIZE, max_images), 1):
            batch = [units[i] for i in idx_group]
            images = [img for u in batch for img in (u.images or [])]
            # A sheet with no image but readable text (e.g. a body whose render
            # failed) still goes to the model — the prompt points at its text.
            if not images and not any(u.text for u in batch):
                continue
            # Sheets grounded by their own text only need LOW image detail.
            detail = "low" if all(u.text for u in batch) else settings.vision_image_detail
            try:
                if provider == "openai":
                    raw = await vision_client._openai_by_images(
                        images, _batch_prompt(email, batch), _SYSTEM_PROMPT,
                        model, detail, api_key)
                else:
                    raw = await vision_client._chat_compatible(
                        provider, images, _batch_prompt(email, batch), _SYSTEM_PROMPT,
                        model, detail)
                parsed = extract_json_from_vllm_response(raw)
                entries = parsed.get("sheets") if isinstance(parsed, dict) else None
                if not isinstance(entries, list):
                    raise ValueError("model reply had no 'sheets' array")
                by_index = {}
                for e in entries:
                    if isinstance(e, dict) and isinstance(e.get("index"), int):
                        by_index[e["index"]] = e
                for pos, sheet_idx in enumerate(idx_group, 1):
                    e = by_index.get(pos) or (entries[pos - 1] if pos - 1 < len(entries)
                                              and isinstance(entries[pos - 1], dict) else None)
                    if e is not None:
                        sheets[sheet_idx] = _normalize_sheet(units[sheet_idx], e)
                batches += 1
            except Exception as exc:
                errors.append(f"batch {batch_no}: {str(exc)[:120]}")

    for idx, u in enumerate(units):
        if sheets[idx] is None:
            sheets[idx] = await _engine_sheet(u)

    method = "vision-batch" if batches else "engine-per-file"
    if batches and any(s is not None for s in sheets) and errors:
        method = "vision-batch+fallback"
    meta = {
        "method": method,
        "model": model if use_vision and batches else None,
        "batches": batches,
        "batch_size": _BATCH_SIZE,
        "sheet_count": len(units),
        "errors": errors[:4],
    }
    boosted = [
        _boost_sheet_from_hints(sheets[i], units[i], email.subject)
        for i in range(len(units))
        if sheets[i] is not None
    ]
    return boosted, meta


# --------------------------------------------------------------------------- #
# 3) Approval — read by the MODEL: signatures on sheets, approval screenshots,
#    approval wording anywhere (the body is one of the analysed sheets).
# --------------------------------------------------------------------------- #
def _detect_approval(email: EmailMessage, sheets: list[dict], used_vision: bool = True) -> dict:
    evidence: list[str] = []
    for s in sheets:
        if s["kind"] == "approval":
            q = f' — "{s["approval_evidence"]}"' if s["approval_evidence"] else ""
            evidence.append(f'approval screenshot "{s["name"]}"{q}')
        elif s["manager_signature"] and s["kind"] in ("timesheet", "leave_certificate"):
            evidence.append(f'manager signature on "{s["name"]}"')
        elif s["approval_evidence"]:
            where = "in the email body" if s["name"] == "(email body)" else f'on "{s["name"]}"'
            evidence.append(f'approval wording {where} — "{s["approval_evidence"]}"')
    if not evidence:
        # Backstop when the model missed approval wording in the thread body.
        body = (email.body_text or "")[:4000]
        if (body and not _NEG_APPROVAL_RE.search(body)
                and not _REQ_APPROVAL_RE.search(body)
                and not _REQ2_APPROVAL_RE.search(body)
                and _POS_APPROVAL_RE.search(body)):
            tag = "pattern match" if used_vision else "pattern match — no API key"
            evidence.append(f"approval wording in the email body ({tag})")
    return {
        "detected": bool(evidence),
        "detail": ("Manager approval: " + "; ".join(evidence) + ".") if evidence
        else "No manager approval found in this email.",
    }


# --------------------------------------------------------------------------- #
# 4) Group per EMPLOYEE + MONTH — the multi-employee differentiator
# --------------------------------------------------------------------------- #
def _tag_for(key: str, month, year) -> str:
    digest = hashlib.sha1(f"{key}|{month or 0}|{year or 0}".encode()).hexdigest()[:12]
    return f"{_TAG_PREFIX}:{digest}"


def _union_group_buckets(members: list[dict]) -> tuple[dict, list[str]]:
    merged: dict[str, list[str]] = {b: [] for b in _BUCKETS}
    flags: list[str] = []
    for b in _BUCKETS:
        seen: dict[str, str] = {}
        for s in members:
            for d in s["buckets"].get(b) or []:
                if d in seen:
                    if b != "public_holiday" and seen[d] != s["name"]:
                        flags.append(
                            f"Date {d} ({b.replace('_', ' ')}) appears on both "
                            f"{seen[d]} and {s['name']} — counted once, please verify.")
                else:
                    seen[d] = s["name"]
        merged[b] = sorted(seen)
    return merged, list(dict.fromkeys(flags))


async def _group_sheets(db: AsyncSession, email: EmailMessage, sheets: list[dict]) -> list[dict]:
    """Group data sheets (timesheets + certificates) by resolved employee, then
    by month/year. Returns group dicts ready for staging."""
    from app.services.pipeline import matching

    data_sheets = [s for s in sheets if s["kind"] in ("timesheet", "leave_certificate")]
    if not data_sheets:
        return []

    # Resolve each sheet's identity against the employee matcher.
    emp_info: dict[str, dict] = {}  # key -> {pk, name, id, note}
    for s in data_sheets:
        key = None
        if s["employee_id"] or s["employee_name"]:
            m = await matching.match_employee(db, s["employee_id"], s["employee_name"])
            if m.employee:
                key = f"pk:{m.employee.id}"
                emp_info.setdefault(key, {
                    "employee_pk": m.employee.id, "name": m.employee.name,
                    "employee_id": m.employee.employee_id, "note": m.note})
            else:
                key = ("raw:" + (s["employee_id"] or "").strip().lower()
                       + "|" + (s["employee_name"] or "").strip().lower())
                emp_info.setdefault(key, {
                    "employee_pk": None, "name": s["employee_name"],
                    "employee_id": s["employee_id"],
                    "note": f"Not in the matcher — sheet says {s['employee_name'] or '?'} "
                            f"({s['employee_id'] or 'no id'}). Pick the employee in Compare & Fix."})
        s["_key"] = key

    known = [k for k in dict.fromkeys(s["_key"] for s in data_sheets) if k]
    fold_notes: list[str] = []

    if not known:
        # Nobody named on any sheet — fall back to the sender.
        from app.services.inbox.employee_match import match_sender
        sm = await match_sender(db, sender_email=email.sender_email, body_text=email.body_text)
        if sm:
            key = f"pk:{sm['employee_pk']}"
            emp_info[key] = {"employee_pk": sm["employee_pk"], "name": sm["employee_name"],
                             "employee_id": sm["employee_id"],
                             "note": f"Matched by sender email ({sm['matched_email']})."}
        else:
            key = "raw:unknown"
            emp_info[key] = {"employee_pk": None, "name": None, "employee_id": None,
                             "note": "No employee could be read from any sheet or the sender — "
                                     "pick the employee in Compare & Fix."}
        for s in data_sheets:
            s["_key"] = key
        known = [key]
    elif len(known) == 1:
        # ONE employee in the email → unidentified sheets (certificates without
        # a name) fold into that employee's item.
        only = known[0]
        for s in data_sheets:
            if not s["_key"]:
                s["_key"] = only
                fold_notes.append(
                    f"{s['name']} carries no readable name/ID — attributed to "
                    f"{emp_info[only]['name'] or 'the matched employee'} because every "
                    "identified sheet in this email belongs to them. Please verify.")
    else:
        # SEVERAL employees → never guess. Unidentified sheets form their own item.
        if any(not s["_key"] for s in data_sheets):
            emp_info["raw:unassigned"] = {
                "employee_pk": None, "name": None, "employee_id": None,
                "note": "This email carries sheets for several employees and these sheets "
                        "show no readable name/ID — assign them manually."}
            for s in data_sheets:
                if not s["_key"]:
                    s["_key"] = "raw:unassigned"

    # Split each employee's sheets by month/year; sheets without a period
    # inherit the employee's majority period.
    groups: list[dict] = []
    for key in dict.fromkeys(s["_key"] for s in data_sheets):
        members = [s for s in data_sheets if s["_key"] == key]
        periods = [(s["month"], s["year"]) for s in members if s["month"] and s["year"]]
        majority = max(set(periods), key=periods.count) if periods else (None, None)
        by_period: dict[tuple, list[dict]] = {}
        for s in members:
            p = (s["month"], s["year"]) if (s["month"] and s["year"]) else majority
            by_period.setdefault(p, []).append(s)
        for (month, year), part in by_period.items():
            buckets, overlap_flags = _union_group_buckets(part)
            groups.append({
                "tag": _tag_for(key, month, year),
                **emp_info[key],
                "month": month, "year": year,
                "buckets": buckets,
                "overlap_flags": overlap_flags,
                "fold_notes": [n for n in fold_notes
                               if any(s["name"] in n for s in part)],
                "sheets": part,
            })
    return groups


# --------------------------------------------------------------------------- #
# 5) Stage — one pending-review item per group, raw copy = the full .eml
# --------------------------------------------------------------------------- #
async def _stage_groups(
    db: AsyncSession, email: EmailMessage, eml_bytes: bytes, eml_name: str,
    groups: list[dict], approval: dict, run_meta: dict,
) -> list[PipelineFile]:
    from app.services.extraction.validation import summarize as summarize_record
    from app.services.extraction.validation import validate
    from app.services.pipeline import raw_store

    msg_id = email.provider_message_id
    existing = (await db.execute(select(PipelineFile).where(
        PipelineFile.source_kind == "email",
        PipelineFile.source_id == msg_id,
        PipelineFile.attachment_id.like(f"{_TAG_PREFIX}%"),
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
        summary = f"{summary} {approval['detail']}"

        display = eml_name if len(groups) == 1 else \
            f"{g['name'] or 'Unassigned sheets'} — {eml_name}"
        tag = g["tag"]
        used.add(tag)
        t = by_tag.get(tag)
        if t is None:
            t = PipelineFile(
                filename=display, content_type="message/rfc822",
                size_bytes=len(eml_bytes), source_kind="email",
                source_id=msg_id, attachment_id=tag)
            db.add(t)
            await db.flush()
        t.filename = display
        if not t.raw_path:
            t.raw_path = raw_store.save_raw(t.id, eml_name, eml_bytes)
        t.employee_id = g["employee_id"]
        t.employee_name = g["name"]
        t.month, t.year = month, year
        t.status = PipelineStatus.NEEDS_REVIEW
        t.failure_code = FailureCode.PENDING_REVIEW
        t.failure_detail = ("Extracted from the full email — review the leaves and accept to file."
                            + (f" ({g['name']})" if g["name"] and len(groups) > 1 else ""))
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
                } for s in g["sheets"]],
            },
            "source_kind": "email",
        }
        total = sum(len(v) for v in cleaned.values())
        t.events = (t.events or []) + [{
            "stage": PipelineStage.EXTRACTION, "status": "ok",
            "detail": (f"Full-email extraction: {len(g['sheets'])} sheet(s) → {total} leave day(s)"
                       f" for {g['name'] or 'unassigned'}. {approval['detail']}"),
            "at": _now_iso(),
        }]
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


async def _mark_no_sheets(db: AsyncSession, email: EmailMessage, note: str) -> None:
    """Persist "Extract Email ran, found nothing to stage" on the email row
    itself so the UI can show a lasting badge/filter instead of the user
    having to re-click Extract Email to rediscover the same empty result."""
    email.no_sheets_found_at = _now()
    email.no_sheets_note = note[:500]
    await db.commit()


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def extract_full_email(db: AsyncSession, email: EmailMessage) -> dict:
    """The whole flow. Returns
    {staged, groups, sheets, employees, approval, message}."""
    from app.services.email_provider import get_email_provider
    from app.services.inbox.eml_export import build_full_eml

    eml_bytes, eml_name = await build_full_eml(get_email_provider(), email)
    units = _collect_units(email, eml_bytes)
    if not units:
        message = "No readable sheets found inside this email."
        await _mark_no_sheets(db, email, message)
        return {"staged": [], "groups": 0, "sheets": [], "employees": [],
                "approval": {"detected": False, "detail": "No sheets to check."},
                "message": message}

    sheets, run_meta = await _analyse_units(email, units)
    approval = _detect_approval(email, sheets,
                                used_vision=run_meta["method"].startswith("vision"))
    groups = await _group_sheets(db, email, sheets)
    if not groups:
        kinds = ", ".join(f"{s['name']} ({s['kind']})" for s in sheets)
        message = f"Nothing to stage — no timesheet or certificate found ({kinds})."
        await _mark_no_sheets(db, email, message)
        return {"staged": [], "groups": 0,
                "sheets": [{"filename": s["name"], "kind": s["kind"],
                            "employee": s["employee_name"]} for s in sheets],
                "employees": [], "approval": approval,
                "message": message}

    # This run DID find something — clear a stale "no sheets" mark from an
    # earlier attempt (e.g. the email was re-processed after attachments
    # became readable).
    if email.no_sheets_found_at is not None:
        email.no_sheets_found_at = None
        email.no_sheets_note = None
        await db.commit()

    staged = await _stage_groups(db, email, eml_bytes, eml_name, groups, approval, run_meta)

    employees = [g["name"] for g in groups if g["name"]]
    n_sheets = sum(len(g["sheets"]) for g in groups)
    if len(groups) == 1:
        who = employees[0] if employees else "an unidentified employee"
        message = (f"{n_sheets} sheet(s) extracted for {who} → 1 item to review. "
                   f"{approval['detail']}")
    else:
        message = (f"{n_sheets} sheet(s) across {len(groups)} employee/month group(s) "
                   f"({', '.join(employees) or 'names pending'}) → {len(groups)} items "
                   f"to review. {approval['detail']}")
    return {
        "staged": staged,
        "groups": len(groups),
        "sheets": [{"filename": s["name"], "kind": s["kind"],
                    "employee": s["employee_name"]} for s in sheets],
        "employees": list(dict.fromkeys(employees)),
        "approval": approval,
        "message": message,
    }
