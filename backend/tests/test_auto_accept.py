"""AI auto-accept decision (ported from the prompt lab's day-accounting gate).

Files a clean, fully-verified group without human review recommendation, and
holds anything short of that with the reason. No per-client-template check —
the same day-by-day accounting gate applies to every document."""
from tests._sheet_helpers import BUCKETS, full_month_sheet, make_sheet

from app.services.extract_email import auto_accept
from app.services.extract_email.grouping import normalise_sheet


def _group_from_sheets(sheets: list[dict], **overrides) -> dict:
    """Build the group shape auto_accept.evaluate() reads, by running each
    sheet through the REAL normalise_sheet() (so _days_in_month /
    _unaccounted_days / _issues are computed exactly like production) and
    unioning them the same way grouping.group_sheets() does."""
    normalised = [normalise_sheet(s) for s in sheets]
    union: dict[str, set] = {b: set() for b in BUCKETS + ("working_days", "weekend_days")}
    uncertain: dict[str, str] = {}
    issues: list[str] = []
    missing: set[int] = set()
    unaccounted: set[str] = set()
    days_covered_total = 0
    for ns in normalised:
        for b in BUCKETS + ("working_days", "weekend_days"):
            union[b] |= set(ns.get(b) or [])
        for u in ns.get("uncertain_days") or []:
            uncertain.setdefault(u["date"], u["reason"])
        issues.extend(ns.get("_issues") or [])
        missing |= {int(d) for d in (ns.get("missing_days") or [])}
        unaccounted |= set(ns.get("_unaccounted_days") or [])
        days_covered_total += int(ns.get("days_covered") or 0)

    g = {
        "employee_pk": "emp-pk-1", "name": "Bhargavi Prabhu", "employee_id": "E2506943",
        "month": sheets[0].get("month"), "year": sheets[0].get("year"),
        "sheets": normalised,
        "buckets": {b: sorted(union[b]) for b in BUCKETS},
        "working_days": sorted(union["working_days"]),
        "weekend_days": sorted(union["weekend_days"]),
        "uncertain_days": [{"date": d, "reason": r} for d, r in sorted(uncertain.items())],
        "issues": list(dict.fromkeys(issues)),
        "missing_days": sorted(missing),
        "unaccounted_days": sorted(unaccounted),
        "days_covered_total": days_covered_total,
        "overlap_flags": [], "fold_notes": [],
    }
    g.update(overrides)
    return g


def _weekend_dates(month: int, year: int) -> set[str]:
    import calendar
    import datetime as dt
    last = calendar.monthrange(year, month)[1]
    return {dt.date(year, month, d).isoformat() for d in range(1, last + 1)
            if dt.date(year, month, d).weekday() >= 5}


def test_clean_full_month_sheet_auto_accepts():
    """Every day placed: worked, weekend, or leave — zero unaccounted, no
    uncertainty."""
    weekend = _weekend_dates(6, 2026)
    sheet = full_month_sheet("TIMESHEET.pdf", 6, 2026,
                              public_holiday=["2026-06-15"], sick=["2026-06-19"])
    # Reassign the weekend days the helper marked as "working" to weekend_days.
    sheet["working_days"] = [d for d in sheet["working_days"] if d not in weekend]
    sheet["weekend_days"] = sorted(weekend - {"2026-06-15", "2026-06-19"})
    g = _group_from_sheets([sheet])
    d = auto_accept.evaluate(g)
    assert d.accepted is True, d.blockers
    assert d.confidence == "high"


def test_unmatched_employee_blocks_auto_accept():
    sheet = full_month_sheet("TIMESHEET.pdf", 6, 2026)
    g = _group_from_sheets([sheet], employee_pk=None)
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("not matched" in b for b in d.blockers)


def test_no_period_blocks_auto_accept():
    sheet = make_sheet("TIMESHEET.pdf", month=None, year=None, period_type="unknown")
    g = _group_from_sheets([sheet])
    g["month"] = g["year"] = None
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("month/year" in b for b in d.blockers)


def test_unaccounted_day_blocks_auto_accept():
    """A full-month sheet missing one day's worth of any classification —
    not leave, not working, not weekend, not uncertain — is held."""
    sheet = full_month_sheet("TIMESHEET.pdf", 6, 2026, sick=["2026-06-19"])
    sheet["working_days"] = [d for d in sheet["working_days"] if d != "2026-06-10"]
    g = _group_from_sheets([sheet])
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("not accounted for" in b for b in d.blockers)


def test_uncertain_day_always_blocks_even_on_an_otherwise_complete_sheet():
    sheet = full_month_sheet("TIMESHEET.pdf", 6, 2026, sick=["2026-06-19"])
    sheet["working_days"].remove("2026-06-10")
    sheet["uncertain_days"] = [{"date": "2026-06-10", "reason": "ambiguous mark"}]
    sheet["days_covered"] = 30
    g = _group_from_sheets([sheet])
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("uncertain" in b.lower() for b in d.blockers)


def test_remote_and_public_holiday_may_coexist_with_working_without_conflict():
    """The one deliberate exception: remote/public_holiday both describe a
    worked day under a particular circumstance — not a genuine conflict."""
    sheet = full_month_sheet("TIMESHEET.pdf", 6, 2026, remote=["2026-06-10"],
                             public_holiday=["2026-06-15"])
    # Also mark the same two dates as worked — must NOT be flagged a conflict.
    if "2026-06-10" not in sheet["working_days"]:
        sheet["working_days"].append("2026-06-10")
    if "2026-06-15" not in sheet["working_days"]:
        sheet["working_days"].append("2026-06-15")
    g = _group_from_sheets([sheet])
    d = auto_accept.evaluate(g)
    assert d.accepted is True, d.blockers
    assert not g["issues"]


def test_sick_and_annual_on_the_same_day_is_a_real_conflict():
    sheet = full_month_sheet("TIMESHEET.pdf", 6, 2026, sick=["2026-06-19"])
    sheet["annual"] = ["2026-06-19"]   # genuinely conflicting bucket, not the exception pair
    g = _group_from_sheets([sheet])
    assert any("two categories" in i for i in g["issues"])
    d = auto_accept.evaluate(g)
    assert d.accepted is False


def test_missing_days_block_auto_accept():
    sheet = make_sheet("TIMESHEET.pdf", month=6, year=2026, days_covered=23,
                       period_type="partial", missing_days=list(range(24, 31)))
    g = _group_from_sheets([sheet])
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("missing" in b.lower() for b in d.blockers)


def test_day_31_not_missing_in_june():
    """June has 30 days. Model often flags blank form cell '31' — strip it."""
    weekend = _weekend_dates(6, 2026)
    sheet = full_month_sheet("TIMESHEET.pdf", 6, 2026,
                              public_holiday=["2026-06-15"])
    sheet["working_days"] = [d for d in sheet["working_days"] if d not in weekend]
    sheet["weekend_days"] = sorted(weekend - {"2026-06-15"})
    sheet["missing_days"] = [31]
    g = _group_from_sheets([sheet])
    assert g["missing_days"] == []
    assert g["sheets"][0]["_days_in_month"] == 30
    d = auto_accept.evaluate(g)
    assert d.accepted is True, d.blockers


def test_four_weekly_sheets_merge_and_auto_accept():
    """4 week files for one month — complementary, day count sums to the
    whole month, no single sheet needs to individually be full_month."""
    weeks = []
    for start, end in [(1, 7), (8, 14), (15, 21), (22, 30)]:
        days = [f"2026-06-{d:02d}" for d in range(start, end + 1)]
        weeks.append(make_sheet(f"week{start}.pdf", month=6, year=2026,
                                days_covered=len(days), period_type="week",
                                working_days=days))
    g = _group_from_sheets(weeks)
    assert g["days_covered_total"] == 30
    d = auto_accept.evaluate(g)
    assert d.accepted is True, d.blockers


def test_leave_certificate_only_group_is_exempt_from_day_grid_coverage():
    """A leave-certificate-only group never claims to cover a whole month —
    it is exempt from the day-accounting coverage check entirely."""
    cert = make_sheet("cert.pdf", kind="leave_certificate", month=6, year=2026,
                      days_covered=0, period_type="partial", sick=["2026-06-19", "2026-06-20"])
    g = _group_from_sheets([cert])
    d = auto_accept.evaluate(g)
    assert d.accepted is True, d.blockers
    assert any("leave-certificate-only" in r for r in d.reasons)


def test_empty_leave_certificate_blocks_auto_accept():
    cert = make_sheet("cert.pdf", kind="leave_certificate", month=6, year=2026,
                      days_covered=0, period_type="partial")
    g = _group_from_sheets([cert])
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("produced no dates" in b for b in d.blockers)
    assert any("leave certificate cert.pdf produced no dates" in b for b in d.blockers)


def test_empty_timesheet_blocker_says_timesheet_not_leave_certificate():
    """Real case: a genuine attendance sheet whose date column doesn't match
    its own stated month (e.g. printed "01-04-2026" under a "MONTH: June"
    header) reads as entirely unusable — Pass 2 correctly refuses to guess
    and returns zero dates. That sheet is still a TIMESHEET; the blocker
    must say so, not call it a "leave certificate" (which it never was and
    misleads the reviewer about what kind of document actually failed)."""
    broken = make_sheet("attendance-sheet.pdf", kind="timesheet", month=6, year=2026,
                        days_covered=0, period_type="partial")
    g = _group_from_sheets([broken])
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("timesheet attendance-sheet.pdf produced no dates" in b for b in d.blockers)
    assert not any("leave certificate" in b for b in d.blockers)


def test_two_full_month_sheets_is_flagged_by_the_overlap_check():
    """Two sheets each claiming the full month is a real duplicate — grouping's
    _multi_sheet_flags catches it as an overlap_flag, which blocks."""
    from app.services.extract_email.grouping import _multi_sheet_flags

    a = full_month_sheet("a.pdf", 6, 2026)
    b = full_month_sheet("b.pdf", 6, 2026)
    normalised = [normalise_sheet(a), normalise_sheet(b)]
    overlap, _fold = _multi_sheet_flags(normalised)
    assert overlap and "SAME full month" in overlap[0]
    g = _group_from_sheets([a, b], overlap_flags=overlap)
    d = auto_accept.evaluate(g)
    assert d.accepted is False
