"""AI auto-accept decision — files a clean, fully-verified group without human
review, and holds anything short of that for Compare & Fix with the reason."""
import calendar

from app.services.extract_email import auto_accept


def _grid_text(month=6, year=2026, weekend=None, holiday=None, sick=None,
               last_day=None):
    """Build ADR-style daily-grid text like the Bhargavi sheet reads (2-digit
    year '1-June-26', exactly as the real PDF prints)."""
    weekend = weekend or []
    holiday = holiday or []
    sick = sick or []
    last = last_day or calendar.monthrange(year, month)[1]
    lines = []
    for d in range(1, last + 1):
        tag = f"{d}-June-{str(year)[2:]}"
        if d in weekend:
            lines.append(f"{tag} Saturday Weekend")
        elif d in holiday:
            lines.append(f"{tag} Public Holiday Public Holiday")
        elif d in sick:
            lines.append(f"{tag} Sick Leave Sick Leave")
        else:
            lines.append(f"{tag} 08:00 AM 5:00 PM 9 9")
    return "\n".join(lines)


def _bhargavi_group(**overrides):
    """A fully-clean ADR group matching the real Bhargavi sheet."""
    weekend = [6, 7, 13, 14, 20, 21, 27, 28]
    text = _grid_text(weekend=weekend, holiday=[15], sick=[19])
    g = {
        "employee_pk": "emp-pk-1", "name": "Bhargavi Prabhu", "employee_id": "E2506943",
        "month": 6, "year": 2026,
        "buckets": {b: [] for b in
                    ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")},
        "overlap_flags": [], "fold_notes": [],
        "sheets": [{"name": "TIMESHEET.pdf", "format_id": "alpha_adr_attendance", "text": text}],
    }
    g["buckets"]["public_holiday"] = ["2026-06-15"]
    g["buckets"]["sick"] = ["2026-06-19"]
    g.update(overrides)
    return g


def test_clean_adr_sheet_auto_accepts():
    d = auto_accept.evaluate(_bhargavi_group())
    assert d.accepted is True, d.blockers
    assert d.confidence == "high"
    assert any("employee matched" in r for r in d.reasons)
    assert any("recognised template" in r for r in d.reasons)
    assert any("coverage verified" in r for r in d.reasons)


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
    # Sheet only covers days 1..23 → 30-day month not fully accounted.
    g = _bhargavi_group()
    g["sheets"][0]["text"] = _grid_text(
        weekend=[6, 7, 13, 14, 20, 21], holiday=[15], sick=[19], last_day=23)
    d = auto_accept.evaluate(g)
    assert d.accepted is False
    assert any(("missing" in b.lower() or "grid" in b.lower()) for b in d.blockers)


def test_no_period_blocks_auto_accept():
    d = auto_accept.evaluate(_bhargavi_group(month=None, year=None))
    assert d.accepted is False
