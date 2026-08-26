"""Roster extraction orchestration: census -> chunked day grid -> reconcile.

Route through here for ANY bulk roster file. The two read strategies are
chosen automatically:

  XLSX / CSV  ->  roster_parse.parse_roster_cells (deterministic, zero LLM
                  calls, exact by construction). Always preferred.
  PDF / image / DOCX  ->  the vision path below.

The vision path's contract, and the reason it can be trusted at 100+ people:

  1. CENSUS every page. This alone decides the headcount. Pages are read
     independently and merged by Sr No, so a roster that runs across four
     pages still yields one list.
  2. Split that list into small batches and read each batch's day cells in
     its own call, bounded so a huge roster doesn't fire 30 requests at once.
  3. RECONCILE: every censused employee must come back from exactly one
     batch. Anyone missing is retried alone (a 1-row call is the most
     accurate shape there is). Anyone STILL missing is reported as an
     explicit issue — never silently dropped.
  4. VERIFY each row against the roster's own printed Leave/Billing totals
     (roster_codes.verify_row_totals). Mismatches ride into the staged row's
     flags, which blocks auto-accept and puts it in front of a human.
"""
from __future__ import annotations

import asyncio

from app.services.bulk_roster import roster_prompt
from app.services.bulk_roster.roster_codes import days_in_month, normalise_code
from app.services.bulk_roster.roster_parse import parse_period, parse_roster_cells
from app.services.bulk_roster.roster_types import RosterDoc, RosterRow

# A roster this big is almost certainly a mis-upload (or would cost far more
# than a human double-check). Refused with a clear message rather than
# quietly truncated — truncation is the one failure mode this flow exists to
# eliminate.
MAX_EMPLOYEES = 300

# Employees per day-grid call. Small on purpose: short replies are where
# transcription stays reliable. 10 rows x 31 days is ~310 cells, comfortably
# inside a reply that doesn't degrade.
GRID_BATCH_SIZE = 10

# How many grid calls may be in flight at once. Caps burst cost/rate-limit
# pressure for a 100-person roster (10 batches) without making it serial.
MAX_CONCURRENT_CALLS = 4

# Rosters are wide: 31 narrow day columns. These render settings are
# deliberately sharper than the on-screen preview defaults, because column
# alignment is exactly what a downscaled image destroys.
RENDER_DPI = 200
RENDER_MAX_WIDTH = 2600
MAX_PAGES = 30


def _page_images(filename: str, data: bytes) -> list[bytes]:
    """Roster pages as JPEGs, rendered for legibility of a wide day grid."""
    from PIL import Image

    from app.services.extraction.file_processor import (
        _office_to_pdf_bytes,
        _to_jpeg_bytes,
        detect_file_type,
        image_to_images,
        to_page_images,
    )

    ftype = detect_file_type(filename, data)
    pdf_bytes: bytes | None = None
    if ftype == "pdf":
        pdf_bytes = data
    elif ftype in ("docx", "xlsx"):
        for ext in ("xlsx", "xls", "docx", "doc"):
            pdf_bytes = _office_to_pdf_bytes(data, ext)
            if pdf_bytes:
                break
    elif ftype == "image":
        return image_to_images(data)

    if not pdf_bytes:
        # No high-DPI path available for this type — the shared preview
        # renderer still produces readable pages.
        return to_page_images(ftype, data)

    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out: list[bytes] = []
    try:
        mat = fitz.Matrix(RENDER_DPI / 72.0, RENDER_DPI / 72.0)
        for i in range(min(doc.page_count, MAX_PAGES)):
            pix = doc.load_page(i).get_pixmap(matrix=mat, alpha=False)
            im = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            if im.width > RENDER_MAX_WIDTH:
                h = max(1, round(im.height * RENDER_MAX_WIDTH / im.width))
                im = im.resize((RENDER_MAX_WIDTH, h), Image.LANCZOS)
            out.append(_to_jpeg_bytes(im))
    finally:
        doc.close()
    if not out:
        raise ValueError("The document has no readable pages.")
    return out


def _as_int(v) -> int | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _as_float(v) -> float | None:
    try:
        if v is None or isinstance(v, bool) or str(v).strip() == "":
            return None
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _clean_str(v) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


async def _census(images: list[bytes], model: str, api_key: str) -> tuple[RosterDoc, int]:
    """Pass 1 — who is on this roster. One call per page, merged by Sr No."""
    from app.services.extraction.vision_client import chat_call, image_block, text_block

    doc = RosterDoc(method="vision-roster", model=model)
    by_key: dict[int, RosterRow] = {}
    calls = 0

    for idx, img in enumerate(images, 1):
        label = (f"page {idx} of {len(images)}" if len(images) > 1 else "the roster page")
        try:
            data = await chat_call(
                roster_prompt.CENSUS_SYSTEM,
                [image_block(img), text_block(roster_prompt.census_user_block(label))],
                model, api_key, label=f"roster-census-p{idx}",
            )
        except Exception as e:
            doc.issues.append(f"page {idx} could not be read for the employee list: {e}")
            continue
        calls += 1
        if not isinstance(data, dict):
            doc.issues.append(f"page {idx} returned an unreadable employee list")
            continue

        if doc.month is None:
            doc.month = _as_int(data.get("month"))
        if doc.year is None:
            doc.year = _as_int(data.get("year"))
        if doc.calendar_days is None:
            doc.calendar_days = _as_int(data.get("calendar_days"))
        if doc.agency is None:
            doc.agency = _clean_str(data.get("agency"))

        seen_on_page = 0
        for e in data.get("employees") or []:
            if not isinstance(e, dict):
                continue
            name = _clean_str(e.get("name"))
            if not name:
                continue
            sr = _as_int(e.get("sr_no")) or (max(by_key) + 1 if by_key else 1)
            seen_on_page += 1
            if sr in by_key:
                # Same Sr No twice across pages is normal for a repeated
                # header row on a continued table — keep the first read.
                continue
            by_key[sr] = RosterRow(
                sr_no=sr, name=name,
                title=_clean_str(e.get("title")),
                location=_clean_str(e.get("location")),
                employee_id=_clean_str(e.get("employee_id")),
                confirmation=_clean_str(e.get("confirmation")),
                stated_leave_days=_as_float(e.get("leave_days")),
                stated_billing_days=_as_float(e.get("billing_days")),
            )

        # The model's own count of rows it could SEE vs rows it actually
        # listed — a cheap tripwire for the exact failure this design exists
        # to catch (a long list quietly getting shortened).
        claimed = _as_int(data.get("rows_visible"))
        if claimed is not None and claimed > seen_on_page:
            doc.issues.append(
                f"page {idx}: {claimed} employee row(s) were visible but only {seen_on_page} "
                f"were listed — the roster may be incomplete, check before accepting")

    doc.rows = [by_key[k] for k in sorted(by_key)]
    return doc, calls


async def _read_grid_batch(
    images: list[bytes], batch: list[RosterRow], dim: int, month_label: str,
    model: str, api_key: str, sem: asyncio.Semaphore, label: str,
) -> dict[int, dict]:
    """Pass 2 — the day cells for one batch. Returns {sr_no: row payload}."""
    from app.services.extraction.vision_client import chat_call, image_block, text_block

    wanted = [(r.sr_no, r.name) for r in batch]
    blocks = [image_block(img) for img in images]
    blocks.append(text_block(roster_prompt.grid_user_block(wanted, dim, month_label)))

    async with sem:
        # enable_thinking=False — same reasoning as thread_prompt.run_pass2_batch:
        # this call is pure literal transcription (copy each cell exactly as
        # printed), the case the codebase already established doesn't need
        # the model's separate reasoning trace. Census (below) keeps it on —
        # discovering who's on the roster is closer to pass 1's judgment
        # call, and completeness there matters more than speed.
        data = await chat_call(
            roster_prompt.GRID_SYSTEM, blocks, model, api_key, label=label,
            enable_thinking=False)

    out: dict[int, dict] = {}
    for row in (data or {}).get("rows") or []:
        if not isinstance(row, dict):
            continue
        sr = _as_int(row.get("sr_no"))
        if sr is None:
            continue
        out[sr] = row
    return out


def _apply_grid(row: RosterRow, payload: dict, dim: int) -> None:
    """Copy a grid reply's day cells onto the census row."""
    days = payload.get("days")
    if not isinstance(days, dict):
        row.issues.append("the day grid for this row could not be read")
        return
    codes: dict[int, str] = {}
    for k, v in days.items():
        d = _as_int(k)
        if d is None or not (1 <= d <= dim):
            continue
        codes[d] = str(v or "").strip()
    row.day_codes = codes
    note = _clean_str(payload.get("notes"))
    if note:
        row.issues.append(f"reader note: {note}")


async def _vision_roster(filename: str, data: bytes) -> RosterDoc:
    from app.services.extract_email.thread_extract import require_vision_configured

    api_key, model = require_vision_configured()
    images = _page_images(filename, data)

    doc, calls = await _census(images, model, api_key)
    if not doc.rows:
        raise ValueError(
            "No employee rows could be read from this document — if it is a single "
            "employee's timesheet, use the normal Upload tab instead.")
    if len(doc.rows) > MAX_EMPLOYEES:
        raise ValueError(
            f"This roster lists {len(doc.rows)} employees, above the {MAX_EMPLOYEES} "
            f"limit for one upload. Split the sheet and upload it in parts.")

    if not (doc.month and doc.year):
        raise ValueError(
            "The month and year of this roster could not be read — re-upload with the "
            "period selected, or use a clearer copy.")
    dim = doc.calendar_days or days_in_month(doc.month, doc.year)
    month_label = f"{doc.month:02d}/{doc.year}"

    sem = asyncio.Semaphore(MAX_CONCURRENT_CALLS)
    batches = [doc.rows[i:i + GRID_BATCH_SIZE] for i in range(0, len(doc.rows), GRID_BATCH_SIZE)]
    results = await asyncio.gather(*(
        _read_grid_batch(images, b, dim, month_label, model, api_key, sem,
                         f"roster-grid-b{i + 1}")
        for i, b in enumerate(batches)
    ), return_exceptions=True)
    calls += len(batches)

    merged: dict[int, dict] = {}
    for i, res in enumerate(results):
        if isinstance(res, BaseException):
            doc.issues.append(f"batch {i + 1} of the day grid failed to read: {res}")
            continue
        merged.update(res)

    by_sr = {r.sr_no: r for r in doc.rows}
    for sr, payload in merged.items():
        if sr in by_sr:
            _apply_grid(by_sr[sr], payload, dim)

    # Reconcile: anyone the census found but no batch returned gets one more
    # attempt, alone. A single-row call is the smallest, most reliable ask we
    # can make, so this recovers the ordinary "one row slipped" case.
    missing = [r for r in doc.rows if not r.day_codes]
    if missing:
        retries = await asyncio.gather(*(
            _read_grid_batch(images, [r], dim, month_label, model, api_key, sem,
                             f"roster-grid-retry-{r.sr_no}")
            for r in missing
        ), return_exceptions=True)
        calls += len(missing)
        for row, res in zip(missing, retries):
            if isinstance(res, BaseException) or not res:
                continue
            payload = res.get(row.sr_no) or (list(res.values())[0] if len(res) == 1 else None)
            if payload:
                _apply_grid(row, payload, dim)

    still_missing = [r for r in doc.rows if not r.day_codes]
    for r in still_missing:
        r.issues.append(
            "no day grid could be read for this row — enter the days manually in Review")
    if still_missing:
        doc.issues.append(
            f"{len(still_missing)} of {len(doc.rows)} employee(s) had no readable day grid: "
            + ", ".join(f"{r.name} (Sr {r.sr_no})" for r in still_missing[:5])
            + (" …" if len(still_missing) > 5 else ""))

    doc.calendar_days = dim
    doc.llm_calls = calls
    return doc


async def extract_roster(
    filename: str, data: bytes, *, month: int | None = None, year: int | None = None,
) -> RosterDoc:
    """Read a bulk roster file into a RosterDoc.

    `month`/`year` override whatever the document says — used when the sheet's
    own title is unreadable or wrong. Raises ValueError with a message meant
    for the uploader whenever the file cannot be treated as a roster at all.
    """
    name = (filename or "").lower()
    doc: RosterDoc | None = None

    if name.endswith((".xlsx", ".xlsm", ".xls", ".csv")):
        try:
            doc = parse_roster_cells(filename, data)
        except Exception as e:
            # A spreadsheet that isn't laid out as a grid we can read
            # deterministically still renders fine as an image — fall back
            # rather than refusing the upload outright.
            doc = await _vision_roster(filename, data)
            doc.issues.append(f"read as an image: the spreadsheet's cells could not be parsed ({e})")

    if doc is None:
        doc = await _vision_roster(filename, data)

    if month:
        doc.month = month
    if year:
        doc.year = year
    if not (doc.month and doc.year):
        # Last resort for the deterministic path, whose title text may sit
        # somewhere parse_period didn't look.
        m, y = parse_period(doc.title or filename)
        doc.month, doc.year = doc.month or m, doc.year or y
    if not (doc.month and doc.year):
        raise ValueError(
            "The month and year of this roster could not be determined — pick the period "
            "on the upload form and try again.")

    if not doc.rows:
        raise ValueError("No employee rows were found in this file.")
    if len(doc.rows) > MAX_EMPLOYEES:
        raise ValueError(
            f"This roster lists {len(doc.rows)} employees, above the {MAX_EMPLOYEES} "
            f"limit for one upload. Split the sheet and upload it in parts.")

    doc.calendar_days = doc.calendar_days or days_in_month(doc.month, doc.year)

    # Deterministic rows arrive with raw cell text; drop obvious non-codes so
    # a stray footnote marker doesn't become an "unrecognised code" issue.
    for r in doc.rows:
        r.day_codes = {d: c for d, c in r.day_codes.items()
                       if normalise_code(c) or str(c or "").strip() == ""}
    return doc
