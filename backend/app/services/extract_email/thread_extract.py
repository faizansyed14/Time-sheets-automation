"""Whole-thread extraction — TWO OpenAI calls for an entire conversation.

Extract Email sends the complete thread: every message body (PII-scrubbed),
every attachment, every image over MIN_IMAGE_BYTES, and any email attached
inside an email, opened recursively. Then:

  PASS 1 — UNDERSTAND (triage_prompt.py). The vision model decides which items
  are really timesheets, whose each one is, which client template it follows,
  whether a manager approved (signature on the sheet, a screenshot, or wording
  in the conversation), and what the thread is about. Logos and banners are
  named as noise here so they never reach extraction.

  PASS 2 — EXTRACT (thread_prompt.py). Only the sheets pass 1 validated are
  read again, by a prompt that does nothing but transcribe names and leave
  dates, with explicit handling for partial months, missing days and empty
  columns.

One call asked to do both did neither well: sheets were invented from passing
mentions while genuine grids were skimmed. Two focused prompts beat one that
has to divide its attention.

The conversation summary is a product of pass 1 — there is no separate
summarisation call.

The result is normalised into the same sheet shape the existing grouping,
staging and auto-accept path already consumes, so filing, the vault and the
review queue are unchanged.
"""
from __future__ import annotations

import email as _email
import email.policy
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.pii import scrub_email_for_llm, scrub_text
from app.models.email_message import EmailMessage
from app.services.extract_email.constants import BUCKETS
from app.services.extract_email.progress import count_llm, emit
from app.services.extract_email.sheet_cache import content_key
from app.services.extract_email.sheet_normalizer import clean_dates

# Hard ceilings so one pathological thread can't build an unsendable request.
MAX_FILES = 25
MAX_IMAGES = 25
MAX_BODY_CHARS = 60_000
IMAGE_TYPES = ("png", "jpg", "jpeg", "gif", "webp", "bmp")
# How deep to open emails-inside-emails. A forward of a forward of a forward is
# real; beyond this it is a mail loop, not a timesheet.
MAX_EML_DEPTH = 4
# A real screenshot of a leave-history/attendance app, a signed page, or an
# approval message runs well into six figures of bytes. Anything under this is
# a signature icon, a social-media button or a tracking pixel — never a
# document worth a vision call. This is a size FLOOR only, applied to real
# attachment/inline images; it never touches the body grid we render ourselves
# (that is only ever rendered when a real <table> was found).
MIN_IMAGE_BYTES = 30 * 1024

# Where a conversation stands. Produced by pass 1 and shown in the inbox — the
# reason there is no separate summarisation call any more.
SUMMARY_STATUSES = (
    "sheet_submitted",       # a timesheet arrived, nothing else yet
    "awaiting_approval",     # someone has been ASKED to approve
    "approved",              # a manager has approved
    "correction_requested",  # a change was asked for
    "chasing",               # reminder / follow-up, no new sheet
    "other",
)


@dataclass
class ThreadPayload:
    """Everything from one conversation, ready for a single model call."""

    files: list[tuple[str, bytes, str]] = field(default_factory=list)   # (name, bytes, ftype)
    images: list[tuple[str, bytes]] = field(default_factory=list)       # (name, bytes)
    bodies: str = ""
    manifest: list[str] = field(default_factory=list)
    format_ids: list[str] = field(default_factory=list)
    # name -> extracted text, kept for the deterministic coverage/auto-accept
    # gate downstream (which reads sheet text, not the model's answer).
    texts: dict[str, str] = field(default_factory=dict)
    # (name, reason) — NOT sent to the model. Two different reasons that must
    # not be conflated: "unsupported" (we can't read this type at all) vs
    # "over_capacity" (a real file that would extract fine, but this thread
    # already has MAX_FILES/MAX_IMAGES). The second one is a genuine capacity
    # problem the user needs to know about, not a shrug-worthy filetype issue.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # name -> sha256 of its bytes. Drives the Extracted/New badge (see
    # sheet_cache.py) — NOT a cache key; nothing is reused from a past run.
    digests: dict[str, str] = field(default_factory=dict)


def _walk_parts(msg):
    """Every leaf part, descending INTO forwarded mail so a timesheet attached
    to a forwarded message is collected too."""
    if msg.get_content_maintype() == "multipart":
        for sub in msg.get_payload():
            if hasattr(sub, "walk"):
                yield from _walk_parts(sub)
        return
    if msg.get_content_maintype() == "message":
        payload = msg.get_payload()
        inner = payload[0] if isinstance(payload, list) and payload else None
        if inner is not None:
            yield from _walk_parts(inner)
        return
    yield msg


def collect_thread_payload(messages: list[tuple[str, bytes]]) -> ThreadPayload:
    """Build the single-call payload from raw .eml bytes per thread message.

    `messages` is [(label, eml_bytes)] oldest first. Everything attached is
    kept for the MODEL to judge what is a signature icon and what is a pasted
    approval screenshot — a filename or content-type heuristic is deliberately
    not used for that call. The one exception is size: real images (leave-app
    screenshots, signed pages) are never under MIN_IMAGE_BYTES, so images
    below it are filtered before the call, not left for the model to decide.
    """
    from app.services.extraction.file_processor import (
        _html_to_text,
        detect_file_type,
        eml_body_to_images,
        extract_document_text,
    )
    from app.services.extract_email.formats import detect_format

    p = ThreadPayload()
    body_chunks: list[str] = []
    seen: set[bytes] = set()          # content hashes — a re-sent file goes once
    import hashlib

    def absorb(label: str, eml_bytes: bytes, depth: int = 0) -> None:
        """Pull everything out of ONE message: body, attachments, and any
        email attached inside it.

        Recursive because a forwarded timesheet can be buried arbitrarily
        deep — an .eml attached to an .eml attached to the thread. MIME-nested
        forwards are handled by _walk_parts, but an .eml attached as a FILE
        (Outlook sends these as application/octet-stream) is just bytes, so it
        has to be re-parsed here or the sheet inside is lost entirely.
        """
        if depth > MAX_EML_DEPTH:
            return
        try:
            msg = _email.message_from_bytes(eml_bytes, policy=_email.policy.compat32)
        except Exception:
            return

        subject = msg.get("Subject") or ""

        # Body text. The plain-text alternative is often just the covering note
        # while the TIMESHEET ITSELF is a pasted HTML table — measured on real
        # mail: 10 <table> elements in the HTML part, zero in the plain part.
        # Sending only text/plain would drop the sheet entirely, so the HTML is
        # flattened (rows → lines, cells → " | ") and used when it is richer.
        plain, html = "", ""
        for part in _walk_parts(msg):
            disp = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in disp:
                continue
            ct = part.get_content_type()
            if ct not in ("text/plain", "text/html"):
                continue
            try:
                raw = part.get_payload(decode=True) or b""
                text = raw.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:
                continue
            if ct == "text/plain" and not plain:
                plain = text
            elif ct == "text/html" and not html:
                html = text

        body_for_model = plain
        has_grid = "<table" in (html or "").lower()
        if html:
            flat = _html_to_text(html)
            if len(flat.strip()) > len((plain or "").strip()):
                body_for_model = flat

        subj_s, body_s = scrub_email_for_llm(subject, body_for_model)
        body_chunks.append(f"===== MESSAGE: {label} =====\nSubject: {subj_s}\n\n{body_s}".strip())

        # A real pasted grid also goes up as a picture: flattening can run
        # cells together, and the rendered image keeps the visual layout
        # (colour-coded leave legends included). Rendered AFTER scrubbing, so
        # redacted values are pixels, never recoverable text.
        if has_grid and len(p.images) < MAX_IMAGES:
            try:
                for idx, img in enumerate(eml_body_to_images(eml_bytes)[:2], 1):
                    if len(p.images) >= MAX_IMAGES:
                        break
                    iname = f"{label} — body grid {idx}"
                    p.images.append((iname, img))
                    p.manifest.append(f"{iname} (rendered email body, {len(img) // 1024} KB)")
                    p.texts[iname] = body_s
            except Exception:
                pass

        for part in _walk_parts(msg):
            disp = str(part.get("Content-Disposition", "")).lower()
            ct = part.get_content_type()
            fname = part.get_filename() or ""
            if ct in ("text/plain", "text/html") and "attachment" not in disp:
                continue
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                continue
            if not payload:
                continue

            digest = hashlib.sha256(payload).digest()
            if digest in seen:
                continue
            seen.add(digest)

            ftype = detect_file_type(fname, payload)
            name = fname or f"part-{len(p.manifest) + 1}"

            if ftype in ("pdf", "docx", "xlsx"):
                if len(p.files) >= MAX_FILES:
                    p.skipped.append((name, "over_capacity"))
                    continue
                p.digests[name] = content_key(payload)
                p.files.append((name, payload, ftype))
                p.manifest.append(f"{name} ({ftype}, {len(payload) // 1024} KB)")
                try:
                    text = extract_document_text(ftype, payload) or ""
                except Exception:
                    text = ""
                p.texts[name] = text
                spec = detect_format(text, name, subject)
                if spec.id != "generic":
                    p.format_ids.append(spec.id)
            elif ftype == "image" or (ct or "").startswith("image/") or \
                    fname.lower().endswith(tuple(f".{e}" for e in IMAGE_TYPES)):
                if len(payload) < MIN_IMAGE_BYTES:
                    p.skipped.append((name, "too_small"))
                    continue
                if len(p.images) >= MAX_IMAGES:
                    p.skipped.append((name, "over_capacity"))
                    continue
                p.digests[name] = content_key(payload)
                p.images.append((name, payload))
                p.manifest.append(f"{name} (image, {len(payload) // 1024} KB)")
            elif ftype == "eml" or fname.lower().endswith(".eml"):
                # An email attached as a FILE. Not MIME-nested, so _walk_parts
                # never sees inside it — re-parse it here or the timesheet it
                # carries is silently dropped (measured: it was).
                p.manifest.append(f"{name} (attached email — opened, contents below)")
                absorb(f"{label} › {name}", payload, depth + 1)
            else:
                # Unknown type: still tell the model it existed.
                p.manifest.append(f"{name} ({ftype or ct}, not sent — unsupported type)")
                p.skipped.append((name, "unsupported_type"))

    for label, eml_bytes in messages:
        absorb(label, eml_bytes)

    p.bodies = scrub_text("\n\n".join(body_chunks))[:MAX_BODY_CHARS]
    p.format_ids = list(dict.fromkeys(p.format_ids))
    return p


def thread_call_available() -> bool:
    """True when the one-call thread read can actually run.

    It has no local equivalent — the whole design is "one model reads the whole
    conversation" — so callers must check this and fall back to the per-sheet
    pipeline (which has a local engine) instead of reporting an empty email.
    """
    from app.services.extraction import vision_client

    key = vision_client.openai_api_key() or ""
    return (settings.extraction_engine == "vision"
            and bool(key) and key.lower() != "change-me")


BODY_ALIASES = ("email body", "body", "message body", "(email body)")


def resolve_source(source: str, payload: "ThreadPayload") -> list[str]:
    """Payload item name(s) a pass-1 `source` refers to.

    Pass 1 is told to name the email body "email body", but the rendered body
    grid is stored under a per-message name ("<subject>.eml — body grid 1").
    Matching those two strings literally found nothing, so pass 2 was handed an
    empty attachment list and reported no leave at all for body-pasted sheets —
    silently, because an empty answer looks like a clean one.
    """
    name = (source or "").strip()
    if not name:
        return []
    known = {n for n, _, _ in payload.files} | {n for n, _ in payload.images}
    if name in known:
        return [name]
    if name.lower() in BODY_ALIASES:
        return [n for n, _ in payload.images if "body grid" in n]
    # Last resort: the model shortened or lengthened the label slightly.
    lowered = name.lower()
    return [n for n in known if lowered in n.lower() or n.lower() in lowered]


def normalise_triage(raw: dict, payload: "ThreadPayload") -> tuple[list[dict], dict, dict, list[str]]:
    """Pass-1 JSON -> (items, approval, summary, noise).

    `items` keeps only what pass 1 could evidence as a real document. A
    "timesheet" with no quoted dated row is downgraded here rather than
    trusted: the prompt asks for evidence, but a prompt is a request, and this
    decides what reaches payroll.
    """
    items: list[dict] = []
    for it in raw.get("items") or []:
        if not isinstance(it, dict):
            continue
        source = str(it.get("source") or "").strip() or "(unknown)"
        kind = str(it.get("kind") or "other").lower()
        if kind not in ("timesheet", "leave_certificate", "approval", "other"):
            kind = "other"
        evidence = str(it.get("evidence") or "").strip()

        # A timesheet claim must come with a row the model actually read.
        if kind == "timesheet" and not evidence:
            kind = "other"

        items.append({
            "source": source,
            "kind": kind,
            "format_id": str(it.get("format_id") or "generic"),
            "employee_name": (str(it.get("employee_name")).strip() or None)
            if it.get("employee_name") else None,
            "employee_id": (str(it.get("employee_id")).strip() or None)
            if it.get("employee_id") else None,
            "period_hint": str(it.get("period_hint") or "").strip(),
            "evidence": evidence,
            "manager_signature": bool(it.get("manager_signature")),
            "signature_evidence": str(it.get("signature_evidence") or "").strip(),
            "notes": str(it.get("notes") or "").strip(),
        })

    ap = raw.get("approval") or {}
    detected = bool(ap.get("detected"))
    evidence = str(ap.get("evidence") or "").strip()
    where = str(ap.get("where") or ("none" if not detected else "")).strip()
    source = str(ap.get("source") or "")

    # A signed sheet IS an approval. The prompt says so, but the model has
    # reported `manager_signature: true` on an item while leaving
    # `approval.detected` false in the same answer — and that inconsistency
    # holds an already-approved timesheet for review. Its own per-item finding
    # is the more specific observation, so it wins.
    if not detected:
        signed = next((i for i in items if i["manager_signature"]), None)
        if signed:
            detected = True
            where = "sheet"
            evidence = (signed["signature_evidence"]
                        or f"manager signature on {signed['source']}")
            source = signed["source"]

    approval = {
        "detected": detected,
        "detail": (f"Approval found: {evidence[:200]}" if detected and evidence
                   else "Approval found." if detected
                   else "No manager approval found in this thread."),
        "evidence": evidence,
        "source": source,
        "where": where,
    }

    s = raw.get("summary") or {}
    status = str(s.get("status") or "other").lower()
    if status not in SUMMARY_STATUSES:
        status = "other"
    summary = {
        "headline": str(s.get("headline") or "").strip(),
        "status": status,
        "narrative": str(s.get("narrative") or "").strip(),
        "action_needed": str(s.get("action_needed") or "").strip(),
    }

    noise = [str(n) for n in (raw.get("noise") or []) if str(n).strip()]
    return items, approval, summary, noise


def _match_triage_item(source: str, triage: list[dict], payload: "ThreadPayload") -> dict:
    """Map a pass-2 sheet label back to its pass-1 triage row."""
    by_source = {t["source"]: t for t in triage}
    if source in by_source:
        return by_source[source]
    hits = set(resolve_source(source, payload))
    if hits:
        for t in triage:
            if set(resolve_source(t["source"], payload)) & hits:
                return t
    lowered = source.lower()
    for t in triage:
        ts = t["source"].lower()
        if lowered in ts or ts in lowered:
            return t
    return {}


def normalise_extraction(
    raw: dict, triage: list[dict], payload: "ThreadPayload",
) -> list[dict]:
    """Pass-2 JSON -> the sheet shape grouping/staging already consume.

    Identity and kind come from pass 1 (which had the whole conversation in
    view); dates and leave come from pass 2 (which read the sheet closely).
    Where pass 2 reports a different printed name, it wins — it was looking
    straight at the header.
    """
    by_source = {t["source"]: t for t in triage}
    sheets: list[dict] = []

    for s in raw.get("sheets") or []:
        if not isinstance(s, dict):
            continue
        source = str(s.get("source") or "").strip() or "(unknown)"
        t = by_source.get(source) or _match_triage_item(source, triage, payload)

        try:
            month = int(s.get("month")) if s.get("month") else None
            month = month if month and 1 <= month <= 12 else None
        except (TypeError, ValueError):
            month = None
        try:
            year = int(s.get("year")) if s.get("year") else None
            year = year if year and 2000 <= year <= 2100 else None
        except (TypeError, ValueError):
            year = None

        buckets = {b: clean_dates(s.get(b), month, year) for b in BUCKETS}
        try:
            days_covered = max(0, int(s.get("days_covered") or 0))
        except (TypeError, ValueError):
            days_covered = 0
        missing = []
        for d in s.get("missing_days") or []:
            try:
                n = int(d)
                if 1 <= n <= 31:
                    missing.append(n)
            except (TypeError, ValueError):
                pass
        period_type = str(s.get("period_type") or "unknown").lower()
        if period_type not in ("full_month", "half_month", "week", "partial", "unknown"):
            period_type = "unknown"

        kind = t.get("kind", "timesheet")
        notes = " ".join(x for x in (t.get("notes", ""), str(s.get("notes") or "")) if x).strip()

        sheets.append({
            "name": source,
            "kind": kind,
            # Pass 2 read the header directly; fall back to pass 1's answer.
            "employee_name": (str(s.get("employee_name")).strip() or None)
            if s.get("employee_name") else t.get("employee_name"),
            "employee_id": (str(s.get("employee_id")).strip() or None)
            if s.get("employee_id") else t.get("employee_id"),
            "month": month,
            "year": year,
            "buckets": buckets,
            "manager_signature": bool(t.get("manager_signature")),
            "approval_evidence": t.get("signature_evidence", ""),
            "format_id": t.get("format_id", "generic"),
            # Deterministic gates downstream (auto-accept day coverage) read the
            # sheet's OWN text, not the model's claim about it. Resolved the
            # same way as the attachments — "email body" is not a literal key.
            "text": next((payload.texts[n] for n in resolve_source(source, payload)
                          if n in payload.texts), ""),
            "days_covered": days_covered,
            "period_type": period_type,
            "missing_days": sorted(set(missing)),
            "dates_complete": period_type == "full_month" and not missing,
            "incomplete_sheet": kind == "timesheet" and period_type != "full_month",
            "evidence": str(s.get("evidence") or "").strip() or t.get("evidence", ""),
            "notes": notes,
        })
    return sheets


async def extract_thread_sheets(
    messages: list[tuple[str, bytes]],
) -> tuple[list[dict], dict, list[dict], dict]:
    """Two passes over one conversation.

      1. UNDERSTAND — the whole thread (bodies, attachments, images, emails
         inside emails) goes to the vision model, which decides what is a
         timesheet, whose it is, which template it follows, whether a manager
         approved, and what the thread is about. Logos and banners are named as
         noise here so they never reach extraction.
      2. EXTRACT — only the sheets pass 1 validated are read again, by a prompt
         that does nothing but transcribe names and leave dates.

    Splitting them is the point: one call asked to both classify and transcribe
    did neither well — sheets were invented from passing mentions while real
    grids were skimmed.

    Returns (sheets, approval, conflicts, run_meta). `run_meta["summary_obj"]`
    carries pass 1's conversation summary — there is no separate summary call.
    """
    from app.services.extract_email.thread_prompt import build_extraction_prompt
    from app.services.extract_email.triage_prompt import build_triage_prompt
    from app.services.extraction import vision_client
    from app.services.extraction.parser import extract_json_from_llm_response

    emit("unpack", "spin", "Collecting the whole thread…")
    payload = collect_thread_payload(messages)
    emit("unpack", "ok",
         f"{len(payload.files)} file(s), {len(payload.images)} image(s) from "
         f"{len(messages)} message(s).",
         files=[n for n, _, _ in payload.files],
         images=[n for n, _ in payload.images])

    empty_meta = {
        "method": "thread-two-pass", "model": None, "calls": 0,
        "sheet_count": 0, "errors": [], "skipped": payload.skipped,
        "conflicts": [], "summary": "", "summary_obj": None,
    }
    if not payload.files and not payload.images and not payload.bodies.strip():
        return [], {"detected": False, "detail": "Nothing readable in this thread."}, [], {
            **empty_meta, "errors": ["empty thread"]}

    if not thread_call_available():
        # Callers gate on thread_call_available() first; this is belt-and-braces.
        return [], {"detected": False, "detail": "AI extraction is not configured."}, [], {
            **empty_meta, "method": "disabled",
            "errors": ["extraction engine is not set to vision"]}

    api_key = vision_client.openai_api_key()
    model = vision_client.model_for(vision_client.vision_provider(), "vision")

    # ---- PASS 1: understand the conversation -----------------------------
    sys1, user1 = build_triage_prompt(manifest=payload.manifest, bodies=payload.bodies)
    emit("pass1", "spin",
         f"Pass 1 of 2 — {model} is reading the whole conversation…",
         pass_no=1, model=model,
         files=[n for n, _, _ in payload.files],
         images=[n for n, _ in payload.images],
         message_count=len(messages),
         body_chars=len(payload.bodies))
    raw1 = await vision_client._openai_thread_call(
        payload.files, payload.images, user1, sys1, model, api_key,
        image_detail=settings.vision_image_detail)
    count_llm()

    parsed1 = extract_json_from_llm_response(raw1)
    if not isinstance(parsed1, dict):
        raise ValueError("pass 1 reply was not a JSON object")
    triage, approval, summary_obj, noise = normalise_triage(parsed1, payload)

    data_items = [t for t in triage if t["kind"] in ("timesheet", "leave_certificate")]
    emit("pass1", "ok",
         f"Pass 1 done — {len(data_items)} timesheet/certificate(s) identified"
         + (f", {len(noise)} logo/banner(s) ignored" if noise else "") + ".",
         pass_no=1,
         # Everything the UI shows for pass 1: what it decided, per item.
         items=[{
             "source": t["source"],
             "kind": t["kind"],
             "employee": t.get("employee_name"),
             "employee_id": t.get("employee_id"),
             "format_id": t.get("format_id"),
             "period": t.get("period_hint"),
             "signature": bool(t.get("manager_signature")),
         } for t in triage],
         employees=[t.get("employee_name") for t in data_items if t.get("employee_name")],
         noise=noise,
         approval=approval,
         summary=summary_obj)

    meta = {
        "method": "thread-two-pass",
        "model": model,
        "calls": 1,
        "sheet_count": 0,
        "errors": [],
        "formats_detected": payload.format_ids,
        "files_sent": [n for n, _, _ in payload.files],
        "images_sent": [n for n, _ in payload.images],
        "skipped": payload.skipped,
        "conflicts": [],
        "noise": noise,
        "summary": summary_obj.get("headline", ""),
        "summary_obj": summary_obj,
        "triage": triage,
    }

    if not data_items:
        # Nothing to extract from — pass 2 would have nothing to read.
        return [], approval, [], meta

    # ---- PASS 2: read only the validated sheets --------------------------
    # Resolve each triaged source to the actual payload item(s) — the body
    # sheet is called "email body" by pass 1 but stored under a per-message
    # render name, and a literal match silently sent pass 2 nothing.
    keep: set[str] = set()
    unresolved: list[str] = []
    for t in data_items:
        hits = resolve_source(t["source"], payload)
        if hits:
            keep.update(hits)
        else:
            unresolved.append(t["source"])

    files2 = [(n, b, f) for (n, b, f) in payload.files if n in keep]
    images2 = [(n, b) for (n, b) in payload.images if n in keep]
    formats2 = [t.get("format_id", "generic") for t in data_items]

    if not files2 and not images2:
        # Nothing resolved — extracting would return an empty answer that reads
        # like a clean one. Say so instead.
        meta["errors"] = [
            "pass 1 named sheets that could not be matched to any attachment: "
            + ", ".join(unresolved[:4])]
        emit("extract", "warn", meta["errors"][0])
        return [], approval, [], meta

    # A body-pasted grid needs its text as well as its picture — the flattened
    # HTML carries rows that a rendered image can crop or blur.
    body_text = payload.bodies if any("body grid" in n for n in keep) else ""

    sys2, user2 = build_extraction_prompt(
        sheets=data_items, format_ids=formats2, body_text=body_text)
    emit("pass2", "spin",
         f"Pass 2 of 2 — extracting leave from {len(data_items)} confirmed sheet(s)…",
         pass_no=2, model=model,
         sheets=[t["source"] for t in data_items],
         sent=sorted(keep))
    raw2 = await vision_client._openai_thread_call(
        files2, images2, user2, sys2, model, api_key,
        image_detail=settings.vision_image_detail)
    count_llm()

    parsed2 = extract_json_from_llm_response(raw2)
    if not isinstance(parsed2, dict):
        raise ValueError("pass 2 reply was not a JSON object")
    sheets = normalise_extraction(parsed2, data_items, payload)

    total_days = sum(sum(len(v) for v in s["buckets"].values()) for s in sheets)
    emit("pass2", "ok",
         f"Pass 2 done — {len(sheets)} sheet(s), {total_days} leave day(s).",
         pass_no=2,
         # Per-sheet result the UI lists live, so the reviewer sees exactly
         # what was pulled out before the record is ever staged.
         results=[{
             "source": s["name"],
             "employee": s.get("employee_name"),
             "employee_id": s.get("employee_id"),
             "month": s.get("month"),
             "year": s.get("year"),
             "days_covered": s.get("days_covered"),
             "period_type": s.get("period_type"),
             "leaves": {k: len(v) for k, v in s["buckets"].items() if v},
             "total_days": sum(len(v) for v in s["buckets"].values()),
         } for s in sheets])

    # Record WHICH attachments were read (for the Extracted/New badge).
    meta["calls"] = 2
    meta["sheet_count"] = len(sheets)
    meta["_fresh_by_digest"] = {
        payload.digests[s["name"]]: s
        for s in sheets if s.get("name") in payload.digests
    }
    return sheets, approval, [], meta
