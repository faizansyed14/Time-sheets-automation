"""Group analysed sheets by employee + month."""
from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.services.extract_email.constants import BUCKETS, TAG_PREFIX

def tag_for(key: str, month, year) -> str:
    digest = hashlib.sha1(f"{key}|{month or 0}|{year or 0}".encode()).hexdigest()[:12]
    return f"{TAG_PREFIX}:{digest}"


def union_group_buckets(members: list[dict]) -> tuple[dict, list[str]]:
    merged: dict[str, list[str]] = {b: [] for b in BUCKETS}
    flags: list[str] = []
    for b in BUCKETS:
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


def _multi_sheet_flags(members: list[dict]) -> tuple[list[str], list[str]]:
    """Split overlap vs informational notes when several sheets share a month.

    Four weekly ADR attachments for one employee are COMPLEMENTARY — merge
    them. Two attachments each claiming a FULL month are a DUPLICATE.
    """
    overlap: list[str] = []
    fold: list[str] = []
    ts = [s for s in members if s.get("kind") == "timesheet"]
    if len(ts) < 2:
        return overlap, fold

    full = [s for s in ts if s.get("period_type") == "full_month"]
    if len(full) >= 2:
        overlap.append(
            "Two sheets claim the SAME full month ("
            + ", ".join(s.get("name") or "?" for s in full[:4])
            + ") — needs a human check.")
        return overlap, fold

    names = ", ".join(s.get("name") or "?" for s in ts[:6])
    fold.append(
        f"{len(ts)} partial/week sheet(s) merged for this month ({names}).")
    return overlap, fold


async def group_sheets(db: AsyncSession, email: EmailMessage, sheets: list[dict]) -> list[dict]:
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
                            f"(client ID {s['employee_id'] or 'none'}). Pick the employee in Review."})
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
                                     "pick the employee in Review."}
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
            buckets, overlap_flags = union_group_buckets(part)
            multi_overlap, multi_fold = _multi_sheet_flags(part)
            overlap_flags = list(dict.fromkeys(overlap_flags + multi_overlap))
            part_folds = [n for n in fold_notes if any(s["name"] in n for s in part)]
            part_folds = list(dict.fromkeys(part_folds + multi_fold))
            from app.services.extract_email.auto_accept import merged_coverage
            from app.services.extract_email.formats import get_format
            fmt_flags: list[str] = []
            for fid in dict.fromkeys(s.get("format_id", "generic") for s in part):
                fmt_flags.extend(get_format(fid).validate(buckets, month, year))
            coverage = merged_coverage({"month": month, "year": year, "sheets": part})
            groups.append({
                "tag": tag_for(key, month, year),
                **emp_info[key],
                "month": month, "year": year,
                "buckets": buckets,
                "coverage": coverage,
                "overlap_flags": overlap_flags + list(dict.fromkeys(fmt_flags)),
                "fold_notes": part_folds,
                "sheets": part,
                "formats": list(dict.fromkeys(
                    s.get("format_id", "generic") for s in part)),
            })
    return groups
