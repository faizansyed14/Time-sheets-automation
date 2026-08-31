"""
Employee Excel Importer.

Parses a .xlsx OR legacy .xls file with TWO sheets (DXB and AUH), normalises
the different header schemas, and upserts every row into all_employee_data.

DXB headers: "Emp ID", "DCO", "Employees Name", "Project",
             "Account Managers Name", "Contact No.", "Email"
AUH headers: "Employee ID", "Full Name", "Project", "Salesman",
             "Mobile Number", "Email ID"
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee

_BATCH_FLUSH = 200

# Columns an import may change on an EXISTING employee. `employee_id` is part
# of the identity key (always equal on a match) and `active` is a deliberate
# admin decision, so a bulk upload never touches either.
_DIFF_FIELDS = (
    "name", "account_manager", "employee_email_id",
    "project", "contact_no", "location", "all_emails",
)
# ACO and DCO arrive in ONE spreadsheet column, so they're diffed as a pair —
# see _changes_for. A row that fills that cell is authoritative for both.
_REF_FIELDS = ("aco_number", "dco_number")


def _norm(s: Any) -> str:
    """Strip whitespace; return empty string for None/NaN."""
    if s is None:
        return ""
    text = str(s).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


# The employee-number at the START of an ID cell. The real sheets append free
# text to it — "E2607411 – On Consultancy Agreement", "E2607433 - Consultancy
# Agreement (Outside Country)" — and the SAME person's suffix is worded
# differently from one export to the next, so the number alone is the stable
# part of the identity.
_ID_CORE_RE = re.compile(r"^\s*([A-Za-z]?\d{4,})")


def _id_core(employee_id: str) -> str:
    """The stable employee-number in an ID cell, or "" when there isn't one.

    "E2607411 – On Consultancy Agreement" -> "E2607411"
    "NA" / "N/A" / "NA - Offshore" / "Replacement for Afsal" -> ""

    A cell with no leading employee-number is a PLACEHOLDER, not an ID: those
    rows were entered before the person had a number issued. Returning "" for
    them lets _resolve_existing below recognise a later file that finally
    supplies the real number as the SAME person, instead of inserting a
    second row beside the provisional one.
    """
    raw = (employee_id or "").strip()
    if not raw:
        return ""
    m = _ID_CORE_RE.match(raw)
    return m.group(1).upper() if m else ""


def _identity_key(employee_id: str, name: str) -> tuple[str, str]:
    return (_id_core(employee_id), _norm_name(name))


_BLANK_REF = {"", "NA", "N/A", "-", "--", "NIL", "NONE"}
# "DCO2409846_YASIN WARAK" / "ACO1808209_MOHAMED ABDULLAH ELIMAM MOHAMED" /
# "DCO-2102158" — prefix, then the number. Anything after it is the employee's
# own name repeated, which we drop.
_REF_RE = re.compile(r"(ACO|DCO)\s*[-_: ]?\s*(\d+)", re.I)


def _split_ref(raw: str) -> tuple[str | None, str | None]:
    """One ACO/DCO cell -> (aco_number, dco_number).

    The real sheet keeps BOTH kinds in a single column named "DCO", telling
    them apart only by the prefix on the value, and appends the employee's
    name: "DCO2409846_YASIN WARAK", "ACO1808209_MOHAMED ABDULLAH ...". So the
    prefix decides which column the number belongs in, and the trailing name is
    dropped — the vault folder already leads with the person's name, and
    "(DCO-2409846_YASIN WARAK)" would just repeat it.

    A bare number with no prefix is treated as a DCO (that's the column's own
    name). "N/A" and friends mean no number at all.
    """
    v = (raw or "").strip()
    if v.upper().replace(" ", "") in _BLANK_REF:
        return None, None
    m = _REF_RE.search(v)
    if m:
        num = m.group(2)
        return (num, None) if m.group(1).upper() == "ACO" else (None, num)
    digits = re.sub(r"\D", "", v.split("_")[0])
    return (None, digits or None)


def _first_email(raw: str) -> str | None:
    if not raw:
        return None
    for addr in re.split(r"[;,]", raw):
        addr = addr.strip()
        if addr:
            return addr
    return None


def _parse_sheet_dxb(ws) -> list[dict]:
    header_row_num = None
    header_idx = {}
    for i, row in enumerate(ws.iter_rows()):
        cells = [_norm(c.value) for c in row]
        if "Emp ID" in cells or "emp id" in [c.lower() for c in cells]:
            header_row_num = i + 1
            for j, h in enumerate(cells):
                header_idx[h.lower().strip()] = j
            break
    if header_row_num is None:
        return []

    records = []
    for i, row in enumerate(ws.iter_rows(min_row=header_row_num + 1)):
        row_num = header_row_num + 1 + i
        cells = [_norm(c.value) for c in row]
        if all(c == "" for c in cells):
            continue

        def gl(keys: list[str]) -> str:
            for k in keys:
                v = cells[header_idx[k]] if k in header_idx and header_idx[k] < len(cells) else ""
                if v:
                    return v
            return ""

        emp_id = gl(["emp id"])
        emp_name = gl(["employees name"])
        # Nothing identifiable at all = a spacer/layout row; ignore it quietly.
        # A row with only ONE of the two IS a data-entry problem, so it's kept
        # and reported as skipped rather than vanishing without trace.
        if not emp_id and not emp_name:
            continue
        aco, dco = _split_ref(gl(["dco", "aco/dco", "aco / dco", "dco number",
                                  "dco no.", "dco no", "aco", "reference"]))
        all_emails_raw = gl(["email"])
        records.append({
            "employee_id": emp_id,
            "name": emp_name,
            "aco_number": aco,
            "dco_number": dco,
            # This row filled the ACO/DCO cell, so it decides BOTH fields —
            # an ACO value must also clear a stale DCO (see _changes_for).
            "_ref_present": bool(aco or dco),
            "project": gl(["project"]),
            "account_manager": gl(["account managers name"]),
            "contact_no": gl(["contact no."]),
            "all_emails": all_emails_raw,
            "employee_email_id": _first_email(all_emails_raw),
            "location": "DXB",
            "_row": row_num,
            "_sheet": ws.title,
        })
    return records


def _parse_sheet_auh(ws) -> list[dict]:
    header_idx: dict[str, int] = {}
    header_row_num = None
    for i, row in enumerate(ws.iter_rows()):
        cells = [_norm(c.value) for c in row]
        low = [c.lower() for c in cells]
        if "employee id" in low or "full name" in low:
            header_row_num = i + 1
            for j, h in enumerate(low):
                header_idx[h.strip()] = j
            break
    if not header_idx:
        return []

    records = []
    for i, row in enumerate(ws.iter_rows(min_row=header_row_num + 1)):
        row_num = header_row_num + 1 + i
        cells = [_norm(c.value) for c in row]
        if all(c == "" for c in cells):
            continue

        def gl(keys: list[str]) -> str:
            for k in keys:
                v = cells[header_idx[k]] if k in header_idx and header_idx[k] < len(cells) else ""
                if v:
                    return v
            return ""

        emp_id = gl(["employee id"])
        emp_name = gl(["full name"])
        if not emp_id and not emp_name:
            continue      # spacer/layout row — see the DXB parser above
        all_emails_raw = gl(["email id", "email"])
        aco, dco = _split_ref(gl(["dco", "aco/dco", "aco / dco", "aco", "reference"]))
        records.append({
            "employee_id": emp_id,
            "name": emp_name,
            "aco_number": aco,
            "dco_number": dco,
            "_ref_present": bool(aco or dco),
            "project": gl(["project"]),
            "account_manager": gl(["salesman"]),
            "contact_no": gl(["mobile number", "contact no."]),
            "all_emails": all_emails_raw,
            "employee_email_id": _first_email(all_emails_raw),
            "location": "AUH",
            "_row": row_num,
            "_sheet": ws.title,
        })
    return records


_XLSX_MAGIC = b"PK"                                    # zip container (Office Open XML)
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"        # legacy .xls, OR an IRM-wrapped .xlsx


class _XlsCell:
    """Adapts one xlrd cell value to openpyxl's Cell.value shape — the ONE
    attribute _parse_sheet_dxb/_parse_sheet_auh actually read."""
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value


class _XlsSheet:
    """Adapts an xlrd Sheet to the .title / .iter_rows(min_row=) shape those
    same two parsers already expect from an openpyxl worksheet — so neither
    parser needed to change to also read legacy .xls files."""

    def __init__(self, sheet):
        self._sheet = sheet
        self.title = sheet.name

    def iter_rows(self, min_row: int = 1):
        for r in range(min_row - 1, self._sheet.nrows):
            yield [_XlsCell(self._sheet.cell_value(r, c)) for c in range(self._sheet.ncols)]


def _reject_if_irm_protected(data: bytes) -> None:
    """An OLE2 file that isn't actually a legacy .xls but Microsoft
    Information Rights Management (IRM / Azure RMS) protected can't be read
    by ANY library — decrypting it needs Excel itself, authenticated as a
    permitted user. Detected by its distinctive DataSpaces/EncryptedPackage
    streams, so the importer can say exactly what's wrong instead of xlrd
    failing with an opaque "Can't find workbook in OLE2 compound document"."""
    try:
        import olefile
    except ImportError:
        return  # best-effort — skip the friendlier message if olefile isn't installed
    try:
        with olefile.OleFileIO(BytesIO(data)) as ole:
            streams = {"/".join(p) for p in ole.listdir()}
    except Exception:
        return
    if any("EncryptedPackage" in s or "DataSpaces" in s for s in streams):
        raise RuntimeError(
            "This file is protected by Microsoft Information Rights Management "
            "(IRM/Azure RMS) — no script can read it, only Excel itself, "
            "authenticated as a permitted user, can. Open it in Excel, then "
            "File → Save As a plain copy (or export to CSV), and import "
            "that copy instead."
        )


def _load_sheets(data: bytes) -> list:
    """.xlsx OR legacy .xls bytes -> a list of sheet-like objects exposing
    .title and .iter_rows(min_row=) — the common shape _parse_sheet_dxb/
    _parse_sheet_auh read, regardless of which format supplied it."""
    if data[:2] == _XLSX_MAGIC:
        try:
            import openpyxl
        except ImportError:
            raise RuntimeError("openpyxl is required to read .xlsx files.")
        wb = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
        return [wb[name] for name in wb.sheetnames]

    if data[:8] == _OLE2_MAGIC:
        _reject_if_irm_protected(data)
        try:
            import xlrd
        except ImportError:
            raise RuntimeError("xlrd is required to read legacy .xls files.")
        book = xlrd.open_workbook(file_contents=data)
        return [_XlsSheet(book.sheet_by_index(i)) for i in range(book.nsheets)]

    raise RuntimeError("Unrecognized file — expected a .xlsx or .xls workbook.")


def parse_workbook(data: bytes) -> list[dict]:
    """.xlsx or legacy .xls bytes -> raw parsed rows (DXB + AUH sheets, normalised headers)."""
    records: list[dict] = []
    for ws in _load_sheets(data):
        name_lower = ws.title.strip().lower()
        if "dxb" in name_lower:
            records.extend(_parse_sheet_dxb(ws))
        elif "auh" in name_lower:
            records.extend(_parse_sheet_auh(ws))
        else:
            r = _parse_sheet_dxb(ws)
            if not r:
                r = _parse_sheet_auh(ws)
            records.extend(r)
    return records


def _split_usable(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """(usable rows, skipped rows). Skips a row with no ID/name, and the second
    occurrence of the same (ID + name) WITHIN this file."""
    usable: list[dict] = []
    skipped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rec in records:
        emp_id = (rec.get("employee_id") or "").strip()
        emp_name = (rec.get("name") or "").strip()
        base = {"sheet": rec.get("_sheet", "Unknown"), "row": rec.get("_row", 0),
                "id": emp_id, "name": emp_name}
        if not emp_id or not emp_name:
            skipped.append({**base, "reason": "Missing ID or Name"})
            continue
        key = _identity_key(emp_id, emp_name)
        if key in seen:
            skipped.append({**base, "reason": "Duplicate ID + Name in file"})
            continue
        seen.add(key)
        usable.append({**rec, "_key": key, "employee_id": emp_id, "name": emp_name})
    return usable, skipped


def _clean(v) -> str | None:
    if v is None:
        return None
    s = v.strip() if isinstance(v, str) else str(v).strip()
    return s or None


def _changes_for(existing: Employee, rec: dict) -> list[dict]:
    """Fields this row would actually change on an existing employee.

    A BLANK cell is "no information", never an instruction to erase — a
    partial sheet must not wipe an email/phone/project the DB already knows.
    Only a non-empty value that differs from what's stored counts.

    ACO/DCO are the one exception, because they share a single column: a row
    that fills it decides BOTH, so an "ACO…" value also CLEARS a DCO stored
    from an earlier read (and vice versa). Without that, a person whose number
    was re-issued under the other prefix would keep both forever and their
    vault folder would claim two contracts.
    """
    out: list[dict] = []
    if rec.get("_ref_present"):
        for f in _REF_FIELDS:
            new = _clean(rec.get(f))
            old = _clean(getattr(existing, f, None))
            if old != new:
                out.append({"field": f, "old": old, "new": new})
    for f in _DIFF_FIELDS:
        new = _clean(rec.get(f))
        if new is None:
            continue
        old = _clean(getattr(existing, f, None))
        if old != new:
            out.append({"field": f, "old": old, "new": new})
    return out


async def _load_employees(db: AsyncSession) -> list[Employee]:
    return list((await db.execute(select(Employee))).scalars().all())


def _index_by_identity(rows: list[Employee]) -> dict[tuple[str, str], Employee]:
    return {_identity_key(e.employee_id, e.name or ""): e for e in rows}


def _index_placeholder_by_name(rows: list[Employee]) -> dict[str, list[Employee]]:
    """Stored rows that have NO usable employee-number, keyed by name — the
    candidates a real ID can be an upgrade of. See _resolve_existing."""
    out: dict[str, list[Employee]] = {}
    for e in rows:
        if not _id_core(e.employee_id):
            out.setdefault(_norm_name(e.name or ""), []).append(e)
    return out


def _resolve_existing(
    index: dict[tuple[str, str], Employee],
    placeholder_by_name: dict[str, list[Employee]],
    rec: dict,
) -> tuple[Employee | None, bool]:
    """Find the stored row this file row is about.

    Returns (row, is_id_upgrade). The second flag marks the case where the
    stored row had a PLACEHOLDER id ("NA") and this file finally supplies the
    real employee-number: the match was made on name alone, so the caller
    must also write the new employee_id — which a normal match never touches
    (it is part of the identity key and equal by definition).

    The name-only fallback is deliberately narrow: it applies only when the
    stored row has no usable number at all, and only when exactly ONE such
    row carries that name. Two people sharing a name would leave the match
    ambiguous, and guessing there could silently fuse two different people —
    so those fall through and are reported as additions instead.
    """
    existing = index.get(rec["_key"])
    if existing is not None:
        return existing, False
    if not rec["_key"][0]:
        # This row has no usable ID either — nothing to upgrade from.
        return None, False
    candidates = placeholder_by_name.get(rec["_key"][1], [])
    if len(candidates) == 1:
        return candidates[0], True
    return None, False


def _existing_out(e: Employee) -> dict:
    return {
        "id": e.id, "employee_id": e.employee_id, "name": e.name,
        "location": e.location, "account_manager": e.account_manager,
        "employee_email_id": e.employee_email_id, "active": e.active,
    }


async def build_import_plan(db: AsyncSession, data: bytes) -> dict:
    """Dry run: exactly what this file WOULD do, without writing anything.

    Returned so a reviewer can confirm before committing:
      to_add            — not in the matcher yet (with a possible-rename hint)
      to_update         — matched, with the exact field-level old -> new diffs
      unchanged         — matched, nothing to change
      missing_from_file — in the matcher but NOT in this file. Never deleted;
                          surfaced so a leaver can be spotted and marked
                          inactive deliberately rather than by a silent purge.
      skipped           — unusable rows (no ID/name, duplicated in the file)
    """
    usable, skipped = _split_usable(parse_workbook(data))
    rows = await _load_employees(db)
    index = _index_by_identity(rows)
    placeholder_by_name = _index_placeholder_by_name(rows)

    # Same ID in the same office under a DIFFERENT name is very likely a
    # rename — flagged, never auto-merged: AUH and DXB reuse ID ranges, so
    # matching on ID alone could silently fuse two different people.
    by_id_loc: dict[tuple[str, str], list[Employee]] = {}
    for e in rows:
        by_id_loc.setdefault(
            (_id_core(e.employee_id), (e.location or "").strip()), []).append(e)

    to_add: list[dict] = []
    to_update: list[dict] = []
    unchanged: list[dict] = []
    matched: set[str] = set()

    for rec in usable:
        existing, id_upgrade = _resolve_existing(index, placeholder_by_name, rec)
        if existing is not None:
            matched.add(existing.id)
            changes = _changes_for(existing, rec)
            if id_upgrade:
                # Matched on name because the stored row had a placeholder id;
                # the real number this file supplies is itself a change.
                changes.insert(0, {"field": "employee_id",
                                   "old": existing.employee_id,
                                   "new": rec["employee_id"]})
            base = {"id": existing.id, "employee_id": existing.employee_id,
                    "name": existing.name, "location": existing.location}
            if changes:
                to_update.append({**base, "changes": changes})
            else:
                unchanged.append(_existing_out(existing))
            continue

        same = by_id_loc.get(
            (_id_core(rec["employee_id"]), (rec.get("location") or "").strip()), [])
        to_add.append({
            "employee_id": rec["employee_id"], "name": rec["name"],
            "location": _clean(rec.get("location")),
            "project": _clean(rec.get("project")),
            "account_manager": _clean(rec.get("account_manager")),
            "employee_email_id": _clean(rec.get("employee_email_id")),
            "contact_no": _clean(rec.get("contact_no")),
            "aco_number": _clean(rec.get("aco_number")),
            "dco_number": _clean(rec.get("dco_number")),
            "sheet": rec.get("_sheet"), "row": rec.get("_row"),
            "possible_rename_of": (
                f"{same[0].name} ({same[0].employee_id})" if same else None),
        })

    missing = sorted(
        (_existing_out(e) for e in rows if e.id not in matched),
        key=lambda r: (r["name"] or "").lower())

    return {
        "to_add": to_add,
        "to_update": to_update,
        "unchanged": unchanged,
        "missing_from_file": missing,
        "skipped": skipped,
    }


async def import_employees_from_bytes(db: AsyncSession, data: bytes) -> dict:
    """Parse xlsx bytes and apply: insert new employees, update changed fields
    on existing ones. Purely additive — an employee absent from the file is
    never deleted or deactivated (see build_import_plan's missing_from_file)."""
    usable, skipped = _split_usable(parse_workbook(data))
    rows = await _load_employees(db)
    index = _index_by_identity(rows)
    placeholder_by_name = _index_placeholder_by_name(rows)

    inserted = updated = touched = 0
    with db.no_autoflush:
        for rec in usable:
            existing, id_upgrade = _resolve_existing(index, placeholder_by_name, rec)
            if existing is not None:
                changes = _changes_for(existing, rec)
                if id_upgrade:
                    # See _resolve_existing: a placeholder-id row finally
                    # getting its real employee-number.
                    changes.insert(0, {"field": "employee_id",
                                       "old": existing.employee_id,
                                       "new": rec["employee_id"]})
                    placeholder_by_name.get(rec["_key"][1], []).remove(existing)
                if not changes:
                    continue
                for c in changes:
                    setattr(existing, c["field"], c["new"])
                # The row's identity key just moved (name and/or id changed);
                # re-index it so a later row in the same file resolves to it
                # rather than inserting a duplicate.
                index[_identity_key(existing.employee_id, existing.name or "")] = existing
                updated += 1
            else:
                row = Employee(
                    employee_id=rec["employee_id"],
                    name=rec["name"],
                    aco_number=_clean(rec.get("aco_number")),
                    dco_number=_clean(rec.get("dco_number")),
                    account_manager=_clean(rec.get("account_manager")),
                    employee_email_id=_clean(rec.get("employee_email_id")),
                    project=_clean(rec.get("project")),
                    contact_no=_clean(rec.get("contact_no")),
                    location=_clean(rec.get("location")),
                    all_emails=_clean(rec.get("all_emails")),
                )
                db.add(row)
                index[rec["_key"]] = row
                inserted += 1

            touched += 1
            if touched % _BATCH_FLUSH == 0:
                await db.flush()

    await db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": len(skipped),
        "skipped_details": skipped,
    }
