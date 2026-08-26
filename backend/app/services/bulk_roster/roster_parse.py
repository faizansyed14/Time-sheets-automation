"""Deterministic roster reader for XLSX / CSV — no LLM involved at all.

When the client sends the roster as a spreadsheet, every cell is already
exact text. Sending that to a vision model would only introduce a chance of
error where there is currently none, so this path reads the cells directly
and is the preferred one whenever the upload is an .xlsx/.xls/.csv.

The layout is discovered rather than hard-coded (find the header row, find
the day columns, find the labelled columns) so a client re-ordering or
re-labelling columns doesn't silently shift the data by one.
"""
from __future__ import annotations

import calendar as _calendar
import csv
import io
import re

from app.services.bulk_roster.roster_types import RosterDoc, RosterRow

_MONTHS = {m.lower(): i for i, m in enumerate(_calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(_calendar.month_abbr) if m})

# "Jul 2026", "July-2026", "07/2026" anywhere in the sheet's title area.
_MONTH_YEAR_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b[\s\-,/]*(\d{4})\b",
    re.I,
)
_NUM_MONTH_YEAR_RE = re.compile(r"\b(0?[1-9]|1[0-2])\s*[/\-]\s*(20\d{2})\b")
_CALENDAR_DAYS_RE = re.compile(r"calendar\s*days?\s*[:\-]?\s*(\d{1,2})", re.I)
# Bounded on purpose: header labels often sit in adjacent cells ("Agency:
# Alphadata" next to "Calendar Days: 31"), and a greedy `(.+)` swallows the
# neighbour's text into the agency name.
_AGENCY_RE = re.compile(
    r"agency\s*[:\-]\s*(.+?)(?=\s{2,}|\s+calendar\s*days|\s+month\b|\s+period\b|$)", re.I)

# Column-header synonyms. Matched on a squashed, lowercased header string, so
# "Emp. Timesheet Confirmation" -> "emptimesheetconfirmation".
_COL_PATTERNS: dict[str, tuple[str, ...]] = {
    "sr_no": ("srno", "sno", "sl", "slno", "serial", "sr"),
    "name": ("resourcename", "employeename", "name", "resource", "consultantname"),
    "employee_id": ("employeeid", "empid", "empcode", "employeecode", "id", "resourceid"),
    "title": ("title", "designation", "role", "position"),
    "location": ("loc", "location", "site", "country"),
    "confirmation": ("emptimesheetconfirmation", "timesheetconfirmation", "confirmation", "status"),
    "leave_days": ("leavedays", "leave", "totalleave", "leavedaystotal"),
    "billing_days": ("billingdays", "billable", "billabledays", "billing", "payabledays"),
}


def _squash(v) -> str:
    return "".join(ch for ch in str(v or "").strip().lower() if ch.isalnum())


def _text(v) -> str:
    return str(v).strip() if v is not None else ""


def _as_number(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _as_day_number(v) -> int | None:
    """A day-header cell -> 1..31. Accepts 1, "01", "1 W", datetimes."""
    if v is None:
        return None
    if hasattr(v, "day") and hasattr(v, "month"):
        return int(v.day)
    n = _as_number(v)
    if n is not None and float(n).is_integer() and 1 <= int(n) <= 31:
        return int(n)
    m = re.match(r"^\s*(\d{1,2})\b", str(v).strip())
    if m and 1 <= int(m.group(1)) <= 31:
        return int(m.group(1))
    return None


def _grid_from_xlsx(data: bytes) -> list[list]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True)
    ws = wb.active
    # Merged title/header cells report their value only in the top-left
    # member; fill the rest so header discovery below sees the real text.
    merged = list(getattr(ws, "merged_cells", []).ranges) if hasattr(ws, "merged_cells") else []
    grid = [[c.value for c in row] for row in ws.iter_rows()]
    for rng in merged:
        try:
            top = grid[rng.min_row - 1][rng.min_col - 1]
        except IndexError:
            continue
        for r in range(rng.min_row - 1, min(rng.max_row, len(grid))):
            for c in range(rng.min_col - 1, rng.max_col):
                if c < len(grid[r]) and grid[r][c] in (None, ""):
                    grid[r][c] = top
    return grid


def _grid_from_csv(data: bytes) -> list[list]:
    text = data.decode("utf-8", errors="replace")
    return [list(row) for row in csv.reader(io.StringIO(text))]


def _find_day_header(grid: list[list]) -> tuple[int, dict[int, int]] | None:
    """Locate the row carrying the 1..31 day columns.

    Returns (row_index, {day_number: column_index}). The winning row is the
    one with the longest run of ASCENDING day numbers — a plain "count of
    cells that look like a day" would happily match a row of leave totals.
    """
    best: tuple[int, dict[int, int]] | None = None
    for r, row in enumerate(grid[:40]):
        found: dict[int, int] = {}
        last = 0
        for c, cell in enumerate(row):
            d = _as_day_number(cell)
            if d is None:
                continue
            # Strictly ascending, and never re-use a day already claimed —
            # keeps a stray "1" in a Sr No column from starting a false run.
            if d == last + 1 or (not found and d == 1):
                found[d] = c
                last = d
        if len(found) >= 20 and (best is None or len(found) > len(best[1])):
            best = (r, found)
    return best


def _find_columns(grid: list[list], day_row: int, first_day_col: int) -> dict[str, int]:
    """Map logical column -> index, searching the header row and the two rows
    above it (rosters often stack a two-line header)."""
    cols: dict[str, int] = {}
    for r in range(max(0, day_row - 2), day_row + 1):
        if r >= len(grid):
            continue
        for c, cell in enumerate(grid[r]):
            if c >= first_day_col:
                break   # never let a day column be claimed as a label column
            key = _squash(cell)
            if not key:
                continue
            for logical, pats in _COL_PATTERNS.items():
                if logical in cols:
                    continue
                if key in pats or any(key == p for p in pats):
                    cols[logical] = c
    # Second, looser pass for headers with extra words ("Total Leave Days").
    for r in range(max(0, day_row - 2), day_row + 1):
        if r >= len(grid):
            continue
        for c, cell in enumerate(grid[r]):
            if c >= first_day_col:
                break
            key = _squash(cell)
            if not key:
                continue
            for logical, pats in _COL_PATTERNS.items():
                if logical in cols:
                    continue
                if any(p in key for p in pats):
                    cols[logical] = c
    return cols


def _read_header_meta(grid: list[list], upto_row: int) -> tuple[str, int | None, str | None]:
    """Title text, calendar-days, agency from everything above the table.

    Labelled values ("Agency: …", "Calendar Days: N") are matched PER CELL,
    not against the joined blob — in a spreadsheet each label is its own
    cell, and matching the blob lets one label's value run into the next.
    The blob is only used for free-text search (the period in the title).
    """
    parts: list[str] = []
    calendar_days: int | None = None
    agency: str | None = None
    for row in grid[: max(1, upto_row)]:
        for cell in row:
            t = _text(cell)
            if not t:
                continue
            parts.append(t)
            if calendar_days is None:
                cal = _CALENDAR_DAYS_RE.search(t)
                if cal:
                    calendar_days = int(cal.group(1))
            if agency is None:
                ag = _AGENCY_RE.search(t)
                if ag and ag.group(1).strip():
                    agency = ag.group(1).strip()[:120]
    return " ".join(dict.fromkeys(parts)), calendar_days, agency


def parse_period(text: str) -> tuple[int | None, int | None]:
    """"…Timesheet Jul 2026 (Contract Hire)" -> (7, 2026)."""
    m = _MONTH_YEAR_RE.search(text or "")
    if m:
        return _MONTHS.get(m.group(1).lower()), int(m.group(2))
    m = _NUM_MONTH_YEAR_RE.search(text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def parse_roster_cells(filename: str, data: bytes) -> RosterDoc:
    """Read an XLSX/CSV roster straight from its cells.

    Raises ValueError when the file plainly isn't a roster grid (no day
    columns found) so the caller can fall back to the vision path rather than
    stage something nonsensical.
    """
    name = (filename or "").lower()
    grid = _grid_from_csv(data) if name.endswith(".csv") else _grid_from_xlsx(data)
    if not grid:
        raise ValueError("The spreadsheet is empty.")

    hit = _find_day_header(grid)
    if not hit:
        raise ValueError(
            "No 1–31 day columns were found — this doesn't look like a multi-employee "
            "roster grid.")
    day_row, day_cols = hit
    first_day_col = min(day_cols.values())
    cols = _find_columns(grid, day_row, first_day_col)
    if "name" not in cols:
        raise ValueError("No employee-name column was found on the roster.")

    blob, calendar_days, agency = _read_header_meta(grid, day_row)
    month, year = parse_period(blob)

    doc = RosterDoc(
        month=month, year=year, calendar_days=calendar_days,
        title=blob[:200] or None, agency=agency, method="xlsx-cells",
    )

    seen_keys: set[str] = set()
    blank_streak = 0
    for r in range(day_row + 1, len(grid)):
        row = grid[r]
        person = _text(row[cols["name"]]) if cols["name"] < len(row) else ""
        if not person:
            blank_streak += 1
            # Tolerate a one-off spacer row, but stop at a real gap so
            # footer/notes blocks below the table are never read as people.
            if blank_streak >= 3:
                break
            continue
        blank_streak = 0

        def cell(logical: str):
            c = cols.get(logical)
            return row[c] if c is not None and c < len(row) else None

        sr = _as_number(cell("sr_no"))
        rr = RosterRow(
            sr_no=int(sr) if sr is not None else len(doc.rows) + 1,
            name=person,
            title=_text(cell("title")) or None,
            location=_text(cell("location")) or None,
            employee_id=_text(cell("employee_id")) or None,
            confirmation=_text(cell("confirmation")) or None,
            stated_leave_days=_as_number(cell("leave_days")),
            stated_billing_days=_as_number(cell("billing_days")),
            day_codes={d: _text(row[c]) for d, c in day_cols.items() if c < len(row)},
        )
        if rr.key in seen_keys:
            doc.issues.append(f"row {rr.sr_no} ({person}) appears more than once — kept once")
            continue
        seen_keys.add(rr.key)
        doc.rows.append(rr)

    if not doc.rows:
        raise ValueError("The roster grid was found but no employee rows could be read from it.")
    return doc
