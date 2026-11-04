"""Normalise pass-2 sheets and group them by employee + month.

Ported from the prompt lab's server-side logic (normalise_sheet /
group_sheets, docs/timesheet_strong_prompt.ipynb §11), extended with REAL
employee identity resolution against the HR master — the notebook's flat
`matcher_names` list was only ever a lab stand-in for this DB-backed match.
"""
from __future__ import annotations

import calendar
import hashlib
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_message import EmailMessage
from app.services.extract_email.constants import BUCKETS, DAY_FIELDS, TAG_PREFIX

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# "remote" and "public_holiday" are not genuine absences the way sick/annual/
# unpaid are — a WFH day IS a worked day (just off-site), and UAE sheets
# routinely show someone clocking in and working ON a public holiday. A date
# claimed by BOTH working_days and one of these is two true facts about the
# same day, not a conflict — every other combination still is.
_COMPATIBLE_WITH_WORKING = {"remote", "public_holiday"}


def tag_for(key: str, month, year) -> str:
    digest = hashlib.sha1(f"{key}|{month or 0}|{year or 0}".encode()).hexdigest()[:12]
    return f"{TAG_PREFIX}:{digest}"


def _categories_compatible(a: str, b: str) -> bool:
    pair = {a, b}
    return "working_days" in pair and bool(pair & _COMPATIBLE_WITH_WORKING)


def _clean_date_list(vals, bucket_name: str, seen: dict[str, str], issues: list[str]) -> list[str]:
    """Shared ISO-validation + cross-bucket-conflict logic for any date list —
    a leave bucket, working_days, or weekend_days. `seen` is shared across ALL
    of a sheet's lists, so a date claimed by two genuinely conflicting
    categories is caught; a compatible pair (see above) is not."""
    if isinstance(vals, str):
        vals = [vals]
    clean = []
    for d in vals or []:
        d = str(d).strip()
        if not _ISO.match(d):
            issues.append(f"non-ISO date dropped: {d!r} ({bucket_name})")
            continue
        if d in seen and seen[d] != bucket_name:
            if _categories_compatible(seen[d], bucket_name):
                continue
            issues.append(f"date in two categories: {d} in {seen[d]} and {bucket_name}")
            continue
        if d in seen:
            continue
        seen[d] = bucket_name
        clean.append(d)
    return sorted(set(clean))


def normalise_sheet(s: dict, calendar_row: dict | None = None) -> dict:
    """ISO-validate every date list, catch cross-bucket conflicts (with the
    remote/public_holiday exception), and — for a full_month sheet — compute
    every day NO bucket accounted for at all (`_unaccounted_days`), the
    deterministic cross-check the auto-accept gate reads instead of trusting
    the model's own completeness claim.

    `calendar_row` (Admin → Month calendars, {"weekend_weekdays": [...]}) —
    when given, a date the model put in BOTH working_days and weekend_days
    (already flagged below as a same-sheet conflict) is resolved in favour
    of weekend_days whenever the admin calendar confirms that date IS a
    configured weekend: that's deterministic ground truth the model already
    contradicted, not something a coin-flip by bucket order should decide.
    """
    out = dict(s)
    issues: list[str] = []
    seen: dict[str, str] = {}
    for b in BUCKETS + DAY_FIELDS:
        out[b] = _clean_date_list(s.get(b), b, seen, issues)

    month, year = out.get("month"), out.get("year")
    if calendar_row and month and year:
        expected_we = expected_weekend_dates(
            int(month), int(year), calendar_row.get("weekend_weekdays") or [])
        wrongly_working = expected_we & set(out.get("working_days") or [])
        if wrongly_working:
            out["working_days"] = sorted(set(out["working_days"]) - wrongly_working)
            out["weekend_days"] = sorted(set(out.get("weekend_days") or []) | wrongly_working)
            issues = [
                iss for iss in issues
                if not any(
                    f"{d} in working_days and weekend_days" in iss
                    or f"{d} in weekend_days and working_days" in iss
                    for d in wrongly_working)
            ]

    clean_uncertain = []
    for u in (s.get("uncertain_days") or []):
        if isinstance(u, str):
            u = {"date": u, "reason": ""}
        d = str(u.get("date", "")).strip()
        if not _ISO.match(d):
            issues.append(f"non-ISO date dropped: {d!r} (uncertain_days)")
            continue
        if d in seen and seen[d] != "uncertain_days":
            issues.append(f"date in two categories: {d} in {seen[d]} and uncertain_days")
            continue
        if d in seen:
            continue
        seen[d] = "uncertain_days"
        clean_uncertain.append({"date": d, "reason": str(u.get("reason", ""))[:200]})
    out["uncertain_days"] = sorted(clean_uncertain, key=lambda u: u["date"])

    out["_days_in_month"] = 0
    out["_unaccounted_days"] = []
    # Drop day numbers the calendar does not have for this month (e.g. 31 in
    # June). Models often flag a blank "31" cell on a fixed 1–31 form layout.
    raw_missing = out.get("missing_days") or []
    if month and year:
        last = calendar.monthrange(int(year), int(month))[1]
        pre = f"{int(year):04d}-{int(month):02d}-"
        for b in BUCKETS + DAY_FIELDS:
            outside = [d for d in out[b] if not d.startswith(pre)]
            if outside:
                issues.append(f"{len(outside)} date(s) outside {pre}* in {b}: {outside[:4]}")
                # kept, not deleted — a leave certificate legitimately spans months
        out["_days_in_month"] = last
        clean_missing: list[int] = []
        for d in raw_missing:
            if not str(d).strip().lstrip("-").isdigit():
                continue
            n = int(d)
            if 1 <= n <= last:
                clean_missing.append(n)
        # A weekly / half-month sheet (or any sheet whose rows span about a week)
        # only claims its OWN span. The model sometimes lists every other day of the
        # month as "missing" for such a file - those days are simply not on this
        # sheet (other weekly files cover them), so they are not gaps. A real gap
        # INSIDE the sheet's own span, and a sheet that silently stops partway
        # through a long period, are kept.
        covered_days = sorted({int(d[8:10]) for b in BUCKETS + DAY_FIELDS for d in out[b]
                               if d.startswith(pre)}
                              | {int(u["date"][8:10]) for u in out["uncertain_days"]
                                 if str(u.get("date", "")).startswith(pre)})
        if (out.get("kind") == "timesheet" and covered_days
                and (out.get("period_type") in ("week", "half_month")
                     or covered_days[-1] - covered_days[0] + 1 <= 8)):
            lo, hi = covered_days[0], covered_days[-1]
            clean_missing = [d for d in clean_missing if lo <= d <= hi]
        out["missing_days"] = sorted(set(clean_missing))
        if out.get("period_type") == "full_month":
            accounted = set()
            for b in BUCKETS + DAY_FIELDS:
                accounted |= set(out[b])
            accounted |= {u["date"] for u in out["uncertain_days"]}
            all_days = {f"{pre}{d:02d}" for d in range(1, last + 1)}
            out["_unaccounted_days"] = sorted(all_days - accounted)
    else:
        # No period — still keep only plausible day-of-month numbers.
        out["missing_days"] = sorted({
            int(d) for d in raw_missing
            if str(d).strip().lstrip("-").isdigit() and 1 <= int(d) <= 31
        })
    out["_issues"] = issues
    return out


def _multi_sheet_flags(members: list[dict]) -> tuple[list[str], list[str]]:
    """Split overlap vs informational notes when several sheets share a
    month. Several weekly/partial attachments for one employee are
    COMPLEMENTARY — merge them. Two attachments each claiming a FULL month
    are a DUPLICATE."""
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
    fold.append(f"{len(ts)} partial/week sheet(s) merged for this month ({names}).")
    return overlap, fold


def _cross_sheet_date_conflicts(normalised: list[dict]) -> list[str]:
    """A date claimed by incompatible categories on DIFFERENT sheets — e.g. a
    weekly attendance sheet says working, a leave certificate for the same
    day says sick. `normalise_sheet` already guarantees each sheet is
    conflict-free ON ITS OWN, but the union step below merges every sheet's
    buckets blind to what any OTHER sheet in the group said, so a
    contradiction spanning two attachments previously reached auto-accept
    with a clean bill of health. Same compatibility exception as within one
    sheet (remote/public_holiday alongside working_days is two true facts,
    not a conflict)."""
    if len(normalised) < 2:
        return []
    seen: dict[str, tuple[str, str]] = {}
    conflicts: list[str] = []
    for ns in normalised:
        name = ns.get("name") or "?"
        for b in BUCKETS + DAY_FIELDS:
            for d in ns.get(b) or []:
                prior = seen.get(d)
                if prior is None:
                    seen[d] = (b, name)
                    continue
                prior_bucket, prior_name = prior
                if prior_bucket == b or _categories_compatible(prior_bucket, b):
                    continue
                conflicts.append(
                    f"date in two categories across sheets: {d} in {prior_bucket} "
                    f"({prior_name}) and {b} ({name})")
    return conflicts


_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def expected_weekend_dates(month: int, year: int, weekend_weekdays: list[str]) -> set[str]:
    """ISO dates in (month, year) whose weekday name is in the admin weekend list."""
    weekend_set = set(weekend_weekdays or [])
    if not weekend_set:
        return set()
    last = calendar.monthrange(year, month)[1]
    return {
        f"{year:04d}-{month:02d}-{d:02d}"
        for d in range(1, last + 1)
        if _WEEKDAY_NAMES[calendar.weekday(year, month, d)] in weekend_set
    }


def calendar_mismatch_flags(month: int, year: int, sheets: list[dict], row: dict) -> list[str]:
    """Compare model weekend_days / public_holiday against Admin → Month calendars.

    Only timesheet grids are checked (leave certificates have no weekend grid).
    Flags become group issues → auto-accept blockers → manual review.
    Empty admin weekend/PH lists mean "not configured" — those sides are skipped.
    """
    ts = [s for s in sheets if s.get("kind") == "timesheet"]
    if not ts or not month or not year:
        return []

    expected_we = expected_weekend_dates(month, year, row.get("weekend_weekdays") or [])
    expected_ph = {str(h.get("date")) for h in (row.get("public_holidays") or []) if h.get("date")}

    pre = f"{int(year):04d}-{int(month):02d}-"
    model_we: set[str] = set()
    model_ph: set[str] = set()
    model_working: set[str] = set()
    leave_dates: set[str] = set()
    # A partial/weekly sheet legitimately only reports a handful of days — a
    # date it never mentions at all is silence, not a contradiction. Only a
    # date the sheet actually said SOMETHING about (worked, weekend, a leave
    # bucket, uncertain, or explicitly "no row" via missing_days) is fair
    # game for a "the AI should have said X but didn't" flag; everything
    # else in the month is simply outside what this sheet ever claimed to
    # cover, full_month or not.
    in_scope: set[str] = set()
    for s in ts:
        model_we |= set(s.get("weekend_days") or [])
        model_ph |= set(s.get("public_holiday") or [])
        model_working |= set(s.get("working_days") or [])
        in_scope |= model_we | model_ph | model_working
        for b in BUCKETS:
            dates = set(s.get(b) or [])
            leave_dates |= dates
            in_scope |= dates
        in_scope |= {u["date"] for u in (s.get("uncertain_days") or [])
                    if isinstance(u, dict) and u.get("date")}
        in_scope |= {f"{pre}{int(d):02d}" for d in (s.get("missing_days") or [])
                    if str(d).strip().isdigit()}

    model_we = {d for d in model_we if d.startswith(pre)}
    model_ph = {d for d in model_ph if d.startswith(pre)}
    model_working = {d for d in model_working if d.startswith(pre)}
    in_scope = {d for d in in_scope if d.startswith(pre)}

    flags: list[str] = []
    if expected_we:
        as_working = sorted(expected_we & model_working)
        if as_working:
            shown = ", ".join(as_working[:8]) + (" …" if len(as_working) > 8 else "")
            flags.append(
                f"calendar mismatch: admin weekend date(s) marked as working by AI: {shown}")
        extra_we = sorted(model_we - expected_we)
        if extra_we:
            shown = ", ".join(extra_we[:8]) + (" …" if len(extra_we) > 8 else "")
            flags.append(
                f"calendar mismatch: AI weekend date(s) not in admin weekend list: {shown}")
        # Expected weekends omitted from weekend_days and not on leave (leave on
        # a weekend is fine). Working case already flagged above. Scoped to
        # in_scope so a partial/weekly sheet isn't flagged for the rest of
        # the month it was never meant to cover.
        missing_we = sorted(
            d for d in (expected_we & in_scope)
            if d not in model_we and d not in leave_dates and d not in model_working)
        if missing_we:
            shown = ", ".join(missing_we[:8]) + (" …" if len(missing_we) > 8 else "")
            flags.append(
                f"calendar mismatch: admin weekend date(s) not listed as weekend by AI: {shown}")

    if expected_ph:
        missing_ph = sorted((expected_ph & in_scope) - model_ph)
        if missing_ph:
            shown = ", ".join(missing_ph[:8]) + (" …" if len(missing_ph) > 8 else "")
            flags.append(
                f"calendar mismatch: admin public holiday(s) missing from AI "
                f"public_holiday: {shown}")
        extra_ph = sorted(model_ph - expected_ph)
        if extra_ph:
            shown = ", ".join(extra_ph[:8]) + (" …" if len(extra_ph) > 8 else "")
            flags.append(
                f"calendar mismatch: AI public_holiday date(s) not in admin calendar: {shown}")
    return flags


_MONTH_LABEL = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ym_of(d) -> tuple[int, int] | None:
    d = str(d or "").strip()
    if not _ISO.match(d):
        return None
    try:
        y, m = int(d[:4]), int(d[5:7])
    except ValueError:
        return None
    return (y, m) if 1 <= m <= 12 else None


def split_sheet_by_month(s: dict) -> list[dict]:
    """One sheet -> one sheet PER CALENDAR MONTH it has dates in.

    Records are per employee + month. A weekly sheet for 29 Jun - 5 Jul, a
    leave certificate for 28 Jun - 3 Jul, or a 26th-25th billing-cycle sheet
    carries days in two months, but the model gives the sheet ONE (month, year).
    Left whole, the other month's days sat in the wrong month's group as
    "outside the month" dates and were never booked to the month they belong to.
    Each shard keeps only its own month's dates, so each month's record is built
    from the right days; nothing is dropped.

    A sheet whose dates all fall in its declared month (the normal case) is
    returned untouched."""
    month, year = s.get("month"), s.get("year")
    fields = BUCKETS + DAY_FIELDS
    by_ym: dict[tuple[int, int], dict[str, list]] = {}
    for b in fields:
        for d in s.get(b) or []:
            ym = _ym_of(d)
            if ym is not None:
                by_ym.setdefault(ym, {}).setdefault(b, []).append(str(d).strip())
    unc_by_ym: dict[tuple[int, int], list] = {}
    for u in s.get("uncertain_days") or []:
        d = u.get("date") if isinstance(u, dict) else u
        ym = _ym_of(d)
        if ym is not None:
            unc_by_ym.setdefault(ym, []).append(u)
    months = set(by_ym) | set(unc_by_ym)
    if not months:
        return [s]
    if not (month and year):
        # The sheet never named its period (a bare certificate): its own dates
        # decide. One month -> stamp it; several -> split like below.
        if len(months) == 1:
            (y0, m0), = months
            return [{**s, "year": y0, "month": m0}]
        declared = None
    elif months <= {(int(year), int(month))}:
        return [s]
    else:
        declared = (int(year), int(month))
    shards: list[dict] = []
    for ym in sorted(months):
        lists = by_ym.get(ym, {})
        shard = dict(s)
        for b in fields:
            shard[b] = sorted(set(lists.get(b, [])))
        shard["uncertain_days"] = unc_by_ym.get(ym, [])
        shard["year"], shard["month"] = ym
        shard["days_covered"] = sum(len(shard[b]) for b in fields)
        if ym != declared:
            shard["period_type"] = "partial"
            shard["missing_days"] = []         # day numbers belong to the declared month
        elif s.get("period_type") == "full_month":
            shard["period_type"] = "partial"   # part of its days now live in the neighbouring month
        if not (shard["days_covered"] or shard["uncertain_days"]):
            continue
        spans = " and ".join(f"{_MONTH_LABEL[m]} {y}" for y, m in sorted(months))
        shard["notes"] = " ".join(x for x in (
            str(s.get("notes") or ""),
            f"[spans {spans} - each month's dates were filed into its own month]") if x).strip()
        shards.append(shard)
    return shards or [s]


async def group_sheets(db: AsyncSession, email: EmailMessage, sheets: list[dict]) -> list[dict]:
    """Resolve identity (DB-backed fuzzy match), group by employee + month,
    normalise every sheet's dates, and union coverage across the group — the
    shape auto_accept.evaluate() reads."""
    from sqlalchemy import select

    from app.models.month_calendar import MonthCalendar
    from app.services.pipeline import matching

    data_sheets = [shard for s in sheets if s["kind"] in ("timesheet", "leave_certificate")
                   for shard in split_sheet_by_month(s)]
    if not data_sheets:
        return []

    cal_rows = (await db.execute(select(MonthCalendar))).scalars().all()
    calendars = {
        (r.month, r.year): {
            "weekend_weekdays": r.weekend_weekdays or [],
            "public_holidays": r.public_holidays or [],
        }
        for r in cal_rows
    }

    # ---- identity resolution against the real HR master --------------------
    emp_info: dict[str, dict] = {}   # key -> {employee_pk, name, employee_id, note}
    for s in data_sheets:
        key = None
        if s["employee_id"] or s["employee_name"]:
            m = await matching.match_employee(
                db, s["employee_id"], s["employee_name"],
                sender_email=getattr(email, "sender_email", None),
                body_text=getattr(email, "body_text", None), corroborate=True)
            if m.employee:
                key = f"pk:{m.employee.id}"
                emp_info.setdefault(key, {
                    "employee_pk": m.employee.id, "name": m.employee.name,
                    "employee_id": m.employee.employee_id, "note": m.note})
                if getattr(m, "low_confidence", False):
                    # Corroborated by ID / sender address but the NAME only partly matches:
                    # a blocker, so it is never recommended for acceptance unseen.
                    emp_info[key].setdefault("match_issues", [])
                    if m.note not in emp_info[key]["match_issues"]:
                        emp_info[key]["match_issues"].append(m.note)
                # Matched on similar spelling alone (no printed ID that agrees, not an
                # exact or short-form name): say so on the item, so a one-letter
                # different person ("Sara" / "Sana") is not accepted unnoticed.
                sheet_id = (s["employee_id"] or "").strip().upper()
                if (s["employee_name"] and sheet_id != (m.employee.employee_id or "").strip().upper()
                        and matching.name_match_strength(s["employee_name"], m.employee.name) == "approximate"):
                    emp_info[key].setdefault("approx_notes", [])
                    note_txt = (f'Name on the sheet "{s["employee_name"]}" is only an approximate match for '
                                f'"{m.employee.name}" in the employee list - check this is the same person.')
                    if note_txt not in emp_info[key]["approx_notes"]:
                        emp_info[key]["approx_notes"].append(note_txt)
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
        # ONE employee in the thread → unidentified sheets (certificates
        # without a name) fold into that employee's item.
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

    # ---- split each employee's sheets by month/year, normalise, union -------
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
            cal_row = calendars.get((month, year)) if month and year else None
            normalised_all = [normalise_sheet({**s, "month": month, "year": year}, cal_row)
                              for s in part]
            groups.append(_build_group(
                key, month, year, normalised_all, calendars, emp_info[key], fold_notes))
    return groups


def _sheet_label(s: dict) -> str:
    return str(s.get("name") or "?")


def _build_group(key: str, month, year, normalised: list[dict], calendars: dict,
                 emp: dict, fold_notes: list[str]) -> dict:
    """One review group (one employee + month) from its normalised sheets."""
    multi_overlap, multi_fold = _multi_sheet_flags(normalised)
    names = {_sheet_label(s) for s in normalised}
    part_folds = [n for n in fold_notes if any(nm in n for nm in names)]
    part_folds = list(dict.fromkeys(part_folds + multi_fold + list(emp.get("approx_notes") or [])))

    tag = tag_for(key, month, year)

    union: dict[str, set] = {b: set() for b in BUCKETS + DAY_FIELDS}
    uncertain: dict[str, str] = {}
    issues: list[str] = []
    missing: set[int] = set()
    unaccounted: set[str] = set()
    days_covered_total = 0
    for ns in normalised:
        for b in BUCKETS + DAY_FIELDS:
            union[b] |= set(ns.get(b) or [])
        for u in ns.get("uncertain_days") or []:
            uncertain.setdefault(u["date"], u["reason"])
        issues.extend(ns.get("_issues") or [])
        missing |= {int(d) for d in (ns.get("missing_days") or [])
                    if str(d).lstrip("-").isdigit()}
        unaccounted |= set(ns.get("_unaccounted_days") or [])
        days_covered_total += int(ns.get("days_covered") or 0)
    issues.extend(emp.get("match_issues") or [])
    issues.extend(_cross_sheet_date_conflicts(normalised))
    if month and year and (month, year) in calendars:
        issues.extend(calendar_mismatch_flags(
            month, year, normalised, calendars[(month, year)]))

    # A day another sheet of this same employee+month covers is not missing.
    covered_nums = {int(d[8:10]) for b in BUCKETS + DAY_FIELDS for d in union[b]
                    if month and year and d.startswith(f"{int(year):04d}-{int(month):02d}-")}
    covered_nums |= {int(d[8:10]) for d in uncertain
                     if month and year and d.startswith(f"{int(year):04d}-{int(month):02d}-")}
    missing -= covered_nums

    return {
        "tag": tag,
        **emp,
        "month": month, "year": year,
        "sheets": normalised,
        **{b: sorted(union[b]) for b in DAY_FIELDS},
        "buckets": {b: sorted(union[b]) for b in BUCKETS},
        "uncertain_days": [{"date": d, "reason": r} for d, r in sorted(uncertain.items())],
        "issues": list(dict.fromkeys(issues)),
        "missing_days": sorted(missing),
        "unaccounted_days": sorted(unaccounted),
        "days_covered_total": days_covered_total,
        "period_types": sorted({s.get("period_type") for s in normalised if s.get("period_type")}),
        "overlap_flags": multi_overlap,
        "fold_notes": part_folds,
    }
