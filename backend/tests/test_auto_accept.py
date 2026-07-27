"""AI auto-accept decision — files a clean, fully-verified group without human
review, and holds anything short of that for Compare & Fix with the reason."""
import calendar

from app.services.extract_email import auto_accept


def _bhargavi_group(**overrides):
    """A fully-clean ADR group — coverage from pass-2 LLM fields, not parser text."""
    g = {
        "employee_pk": "emp-pk-1", "name": "Bhargavi Prabhu", "employee_id": "E2506943",
        "month": 6, "year": 2026,
        "buckets": {b: [] for b in
                    ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")},
        "overlap_flags": [], "fold_notes": [],
        "sheets": [{
            "name": "TIMESHEET.pdf", "kind": "timesheet",
            "format_id": "alpha_adr_attendance",
            "days_covered": 30, "period_type": "full_month",
            "missing_days": [], "dates_complete": True,
        }],
        "coverage": {
            "days_covered": 30, "missing_days": [], "period_type": "full_month",
            "dates_complete": True, "sheet_count": 1,
        },
    }
    g["buckets"]["public_holiday"] = ["2026-06-15"]
    g["buckets"]["sick"] = ["2026-06-19"]
    g.update(overrides)
    return g


def test_clean_adr_sheet_auto_accepts():
    d = auto_accept.evaluate(_bhargavi_group())
    assert d.accepted is True, d.blockers
    assert d.confidence == "high"
    assert any("model" in r.lower() or "coverage" in r.lower() for r in d.reasons)


def test_unmatched_employee_blocks_auto_accept():
    d = auto_accept.evaluate(_bhargavi_group(employee_pk=None))
    assert d.accepted is False
    assert any("not matched" in b for b in d.blockers)


def test_validation_flag_blocks_auto_accept():
    d = auto_accept.evaluate(_bhargavi_group(), extra_flags=["Date 2026-06-40 is out of month."])
    assert d.accepted is False
    assert any("validation flags" in b for b in d.blockers)


def test_generic_template_blocks_auto_accept():
    g = _bhargavi_group()
    g["sheets"][0]["format_id"] = "generic"
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("unrecognised" in b for b in d.blockers)


def test_missing_days_block_auto_accept():
    g = _bhargavi_group()
    g["sheets"][0]["days_covered"] = 23
    g["sheets"][0]["period_type"] = "partial"
    g["sheets"][0]["missing_days"] = list(range(24, 31))
    g["sheets"][0]["dates_complete"] = False
    g["coverage"] = auto_accept.merged_coverage(g)
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("missing" in b.lower() for b in d.blockers)


def test_no_period_blocks_auto_accept():
    d = auto_accept.evaluate(_bhargavi_group(month=None, year=None))
    assert d.accepted is False


def test_four_weekly_sheets_merge_and_auto_accept():
    """4 ADR week files for one month — complementary, not duplicate."""
    weeks = []
    for i, (start, end) in enumerate([(1, 7), (8, 14), (15, 21), (22, 30)], 1):
        days = list(range(start, end + 1))
        weeks.append({
            "name": f"week{i}.pdf", "kind": "timesheet",
            "format_id": "alpha_adr_attendance",
            "days_covered": len(days), "period_type": "week",
            "missing_days": [], "dates_complete": False,
        })
    g = _bhargavi_group(sheets=weeks)
    g["coverage"] = auto_accept.merged_coverage(g)
    assert g["coverage"]["complementary"] is True
    assert g["coverage"]["dates_complete"] is True
    d = auto_accept.evaluate(g)
    assert d.accepted is True, d.blockers


def test_two_full_month_sheets_block():
    g = _bhargavi_group(sheets=[
        {"name": "a.pdf", "kind": "timesheet", "format_id": "alpha_adr_attendance",
         "days_covered": 30, "period_type": "full_month", "missing_days": [], "dates_complete": True},
        {"name": "b.pdf", "kind": "timesheet", "format_id": "alpha_adr_attendance",
         "days_covered": 30, "period_type": "full_month", "missing_days": [], "dates_complete": True},
    ])
    g["coverage"] = auto_accept.merged_coverage(g)
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("full month" in b.lower() for b in d.blockers)


def test_empty_leave_certificate_blocks_auto_accept():
    g = _bhargavi_group(sheets=[
        {"name": "TIMESHEET.pdf", "kind": "timesheet",
         "format_id": "digital_dubai_report",
         "days_covered": 30, "period_type": "full_month",
         "missing_days": [], "dates_complete": True,
         "buckets": {b: [] for b in
                     ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")}},
        {"name": "image018.jpg", "kind": "leave_certificate",
         "format_id": "leave_certificate",
         "days_covered": 0, "period_type": "partial",
         "missing_days": [], "dates_complete": False,
         "buckets": {b: [] for b in
                     ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")}},
    ])
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any("leave evidence" in b for b in d.blockers)


def test_merged_coverage_function():
    g = {"month": 6, "year": 2026, "sheets": [
        {"kind": "timesheet", "days_covered": 7, "period_type": "week", "missing_days": []},
        {"kind": "timesheet", "days_covered": 7, "period_type": "week", "missing_days": []},
        {"kind": "timesheet", "days_covered": 8, "period_type": "week", "missing_days": []},
        {"kind": "timesheet", "days_covered": 8, "period_type": "week", "missing_days": []},
    ]}
    cov = auto_accept.merged_coverage(g)
    assert cov["days_covered"] == 30
    assert cov["complementary"] is True
