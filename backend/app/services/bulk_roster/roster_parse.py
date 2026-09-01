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
import datetime as _datetime
import io
import re

from app.services.bulk_roster.roster_codes import iso, translate_attendance_type
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

# Column-header synonyms for the LONG format — one row per employee PER DAY
# (e.g. a "PGC"-style attendance export), a completely different shape from
# the wide day-grid above (one row per employee, day-number columns across
# the row). Detected separately in _detect_long_format(); every one of
# _LONG_REQUIRED must be found for a sheet to count as long-format — a
# deliberately narrow fingerprint so this can never misfire on the wide
# format's own header (which has no per-row date/attendance-type column at
# all — it has 31 separate numbered day columns instead).
_LONG_COL_PATTERNS: dict[str, tuple[str, ...]] = {
    "employee_id": ("employeeid", "empid", "empcode", "employeecode", "id"),
    "name": ("employeename", "name", "resourcename"),
    "title": ("jobtitle", "designation", "title", "role", "position"),
    "date": ("attendancedate", "date"),
    "attendance_type": ("attendancetype", "type", "status", "daytype"),
}
_LONG_REQUIRED = ("employee_id", "name", "date", "attendance_type")


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


_LONG_DATE_FORMATS = ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y")


def _parse_long_date_cell(v) -> tuple[int, int, int] | None:
    """A long-format AttendanceDate cell -> (day, month, year). openpyxl
    (data_only=True) hands back either a real date/datetime object or a
    string like "01-Jul-2026", depending on how the source cell was typed —
    handle both."""
    if v is None:
        return None
    if hasattr(v, "day") and hasattr(v, "month") and hasattr(v, "year"):
        return int(v.day), int(v.month), int(v.year)
    s = str(v).strip()
    if not s:
        return None
    for fmt in _LONG_DATE_FORMATS:
        try:
            d = _datetime.datetime.strptime(s, fmt)
            return d.day, d.month, d.year
        except ValueError:
            continue
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


_XLSX_MAGIC = b"PK"                                     # zip container (Office Open XML)
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"          # legacy .xls, OR an IRM-wrapped .xlsx


def _grid_from_xls(data: bytes) -> list[list]:
    """Legacy .xls (BIFF) bytes -> the same list[list] grid shape
    _grid_from_xlsx produces — openpyxl cannot read this format at all, so
    this goes through xlrd instead (same library already used for the
    Employee Matcher's own .xls support, see services/employee/import_service.py)."""
    try:
        import xlrd
    except ImportError:
        raise RuntimeError("xlrd is required to read legacy .xls files.")
    book = xlrd.open_workbook(file_contents=data)
    sheet = book.sheet_by_index(0)
    grid = [[sheet.cell_value(r, c) for c in range(sheet.ncols)] for r in range(sheet.nrows)]
    # Merged cells: xlrd reports the value only in the top-left member, same
    # as openpyxl — fill the rest so header discovery below sees real text.
    for (rlo, rhi, clo, chi) in getattr(sheet, "merged_cells", []):
        if rlo >= len(grid) or clo >= len(grid[rlo]):
            continue
        top = grid[rlo][clo]
        for r in range(rlo, min(rhi, len(grid))):
            for c in range(clo, min(chi, len(grid[r]))):
                if grid[r][c] in (None, ""):
                    grid[r][c] = top
    return grid


def _reject_if_irm_protected(data: bytes) -> None:
    """An OLE2 file that isn't actually a legacy .xls but Microsoft
    Information Rights Management (IRM/Azure RMS) protected can't be read by
    ANY library — decrypting it needs Excel itself, authenticated as a
    permitted user. Detected by its distinctive DataSpaces/EncryptedPackage
    streams, so this can say exactly what's wrong instead of xlrd failing
    with an opaque "Can't find workbook in OLE2 compound document". Same
    check as services/employee/import_service.py's own — kept as a small
    local copy rather than a cross-module import of a private helper, since
    it's a fixed byte/stream-name signature, not prose that could drift."""
    try:
        import olefile
    except ImportError:
        return  # best-effort — skip the friendlier message if olefile isn't installed
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            streams = {"/".join(p) for p in ole.listdir()}
    except Exception:
        return
    if any("EncryptedPackage" in s or "DataSpaces" in s for s in streams):
        raise RuntimeError(
            "This file is protected by Microsoft Information Rights Management "
            "(IRM/Azure RMS) — no script can read it, only Excel itself, "
            "authenticated as a permitted user, can. Open it in Excel, then "
            "File → Save As a plain copy (or export to CSV), and import that "
            "copy instead."
        )


def _load_grid(filename: str, data: bytes) -> list[list]:
    """Any supported roster file (.xlsx/.xlsm/.xls/.csv) -> the same
    list[list] grid shape, regardless of which format supplied it. Format is
    decided by the bytes' own magic number, not the filename extension —
    a mislabeled or renamed file still reads correctly, and a genuine
    IRM-protected file gets a clear, specific error instead of an opaque
    crash deep inside xlrd/openpyxl."""
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return _grid_from_csv(data)
    if data[:8] == _OLE2_MAGIC:
        _reject_if_irm_protected(data)
        return _grid_from_xls(data)
    return _grid_from_xlsx(data)


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


def _detect_long_format(grid: list[list]) -> tuple[int, dict[str, int]] | None:
    """Find the long-format header row (all of _LONG_REQUIRED present) within
    the first few rows. Returns (row_index, {logical: column_index}) or None
    — this sheet just isn't laid out that way."""
    for r, row in enumerate(grid[:5]):
        cols: dict[str, int] = {}
        for c, cell in enumerate(row):
            key = _squash(cell)
            if not key:
                continue
            for logical, pats in _LONG_COL_PATTERNS.items():
                if logical in cols:
                    continue
                if key in pats:
                    cols[logical] = c
        if all(k in cols for k in _LONG_REQUIRED):
            return r, cols
    return None


def _parse_long_format(grid: list[list], header_row: int, cols: dict[str, int]) -> RosterDoc:
    """Pivot a long/flat (one row per employee PER DAY) sheet — e.g. a "PGC"-
    style attendance export — into the same RosterDoc/RosterRow shape the
    wide-format reader produces below, so every downstream step (bucket
    distribution, employee matching, staging) works completely unchanged.

    Unlike the wide format, this shape carries no document-level title,
    calendar-days, or leave/billing-days totals to read — month/year are
    derived from the AttendanceDate values themselves, and every RosterRow's
    stated_leave_days/stated_billing_days stay None (verify_row_totals()
    already no-ops when those are None, so this degrades gracefully rather
    than needing new code there).
    """
    # Pass 1: read every row into a flat, uninterpreted record. This lets the
    # document's own month/year be determined (the majority (month, year)
    # across every AttendanceDate) BEFORE any day gets assigned to a grid
    # position, so a stray row from a different month/year can be recognised
    # and flagged rather than silently landing on the wrong date.
    records: list[dict] = []
    month_year_counts: dict[tuple[int, int], int] = {}
    for row in grid[header_row + 1:]:
        def cell(logical: str):
            c = cols.get(logical)
            return row[c] if c is not None and c < len(row) else None

        person = _text(cell("name"))
        if not person:
            continue
        date_parts = _parse_long_date_cell(cell("date"))
        records.append({
            "name": person,
            "employee_id": _text(cell("employee_id")) or None,
            "title": _text(cell("title")) or None,
            "date_parts": date_parts,
            "attendance_type": _text(cell("attendance_type")),
        })
        if date_parts:
            my = (date_parts[1], date_parts[2])
            month_year_counts[my] = month_year_counts.get(my, 0) + 1

    if not records:
        raise ValueError("The long-format sheet was found but no employee rows could be read from it.")

    month, year = (max(month_year_counts, key=month_year_counts.get)
                   if month_year_counts else (None, None))

    # Pass 2: group by EmployeeId (falling back to normalised name when
    # blank), pivot AttendanceDate -> day_codes. Every distinct employee
    # becomes a RosterRow unconditionally — never silently dropped, even
    # when a row within their block has a bad date or an already-claimed day.
    groups: dict[str, RosterRow] = {}
    order: list[str] = []

    for rec in records:
        emp_id = rec["employee_id"]
        key = emp_id or f"name:{rec['name'].strip().lower()}"
        if key not in groups:
            order.append(key)
            groups[key] = RosterRow(sr_no=0, name=rec["name"], title=rec["title"], employee_id=emp_id)
        rr = groups[key]
        if not rr.title and rec["title"]:
            rr.title = rec["title"]

        dp = rec["date_parts"]
        if dp is None:
            rr.issues.append(f"a row for {rec['name']} had no readable AttendanceDate — skipped")
            continue
        day, mth, yr = dp
        if month and (mth, yr) != (month, year):
            rr.issues.append(
                f"a row dated {yr:04d}-{mth:02d}-{day:02d} doesn't belong to "
                f"{_calendar.month_name[month]} {year} — skipped")
            continue
        if day in rr.day_codes:
            rr.issues.append(f"day {day} appears more than once for {rec['name']} — kept the first")
            continue

        code, flag = translate_attendance_type(rec["attendance_type"])
        rr.day_codes[day] = code
        if flag:
            rr.issues.append(f"{iso(year, month, day)}: {flag}")

    doc = RosterDoc(month=month, year=year, method="xlsx-cells-long")
    for i, key in enumerate(order, 1):
        rr = groups[key]
        rr.sr_no = int(rr.employee_id) if rr.employee_id and rr.employee_id.isdigit() else i
        doc.rows.append(rr)
    return doc


def looks_like_roster_grid(filename: str, data: bytes) -> bool:
    """True when this file's cells are laid out like a multi-employee roster
    (either shape parse_roster_cells() understands — wide day-grid or long
    per-day-row). Used as a cheap, deterministic guard on the SINGLE-employee
    /upload path (see api/routes/upload.py) so a roster dropped there by
    mistake is caught before it ever reaches the vision pipeline — no
    LibreOffice conversion, no images, no LLM call spent finding out.

    False on anything that fails to read as a spreadsheet at all, or isn't
    one of the extensions this reader handles — that's a different problem,
    not this guard's job to catch; the normal pipeline deals with it as it
    always has.
    """
    name = (filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm", ".xls", ".csv")):
        return False
    try:
        grid = _load_grid(filename, data)
    except Exception:
        return False
    if not grid:
        return False
    if _detect_long_format(grid) is not None:
        return True
    return _find_day_header(grid) is not None


def parse_roster_cells(filename: str, data: bytes) -> RosterDoc:
    """Read an XLSX/XLS/CSV roster straight from its cells.

    Raises ValueError when the file plainly isn't a roster grid (no day
    columns found, in either the wide or long layout) so the caller can fall
    back to the vision path rather than stage something nonsensical.
    """
    grid = _load_grid(filename, data)
    if not grid:
        raise ValueError("The spreadsheet is empty.")

    # Long format (one row per employee PER DAY) is checked FIRST — a
    # deliberately narrow, four-column fingerprint (see _LONG_REQUIRED) that
    # can never match the wide format's own header, so this never steals a
    # sheet the wide-grid logic below should handle.
    long_hit = _detect_long_format(grid)
    if long_hit is not None:
        header_row, cols = long_hit
        return _parse_long_format(grid, header_row, cols)

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
