"""Weekly sheets of one month: a week is not "missing" the rest of the month.

A real email carried four weekly screenshots (5-11, 12-18, 19-25, 26 Sep-2 Oct) for
one employee. The model listed days 1-25 as "missing" on the last one; that surfaced
as "25 missing days" on the merged item although the other weeks cover them. Gaps
that ARE real - a missing row inside a sheet's own span, or a sheet that silently
stops partway through a long period - must still be reported.
"""
from app.services.extract_email.grouping import group_sheets, normalise_sheet
from app.services.extract_email.staging import group_flags


def _sheet(name, days, *, period_type="week", missing=(), **over):
    base = {
        "kind": "timesheet", "name": name, "employee_name": "Week Person",
        "employee_id": "E-WK-1", "month": 9, "year": 2026, "period_type": period_type,
        "days_covered": len(days), "missing_days": list(missing),
        "working_days": [f"2026-09-{d:02d}" for d in days], "weekend_days": [],
        "uncertain_days": [], "annual": [], "remote": [], "sick": [], "maternity": [],
        "unpaid": [], "absent": [], "public_holiday": [], "other": [], "notes": "",
    }
    base.update(over)
    return base


def test_a_week_does_not_report_the_rest_of_the_month_as_missing():
    s = _sheet("wk4.png", range(24, 31), period_type="partial", missing=range(1, 24))
    assert normalise_sheet(s)["missing_days"] == []
    s = _sheet("wk4.png", range(24, 31), period_type="week", missing=range(1, 24))
    assert normalise_sheet(s)["missing_days"] == []


def test_a_real_gap_inside_the_weeks_own_span_is_kept():
    s = _sheet("wk.png", [7, 8, 10, 11], period_type="week", missing=[9, 1, 2, 20])
    assert normalise_sheet(s)["missing_days"] == [9]


def test_a_sheet_that_silently_stops_partway_is_still_reported():
    s = _sheet("long.png", range(1, 21), period_type="partial", missing=range(21, 31))
    assert normalise_sheet(s)["missing_days"] == list(range(21, 31))


def test_a_full_month_sheet_keeps_its_gaps():
    s = _sheet("month.png", [d for d in range(1, 31) if d != 15], period_type="full_month", missing=[15])
    assert normalise_sheet(s)["missing_days"] == [15]


async def test_four_weekly_sheets_merge_with_no_false_missing_day_flags():
    from app.core.database import SessionLocal

    sheets = [
        _sheet("wk1.png", range(5, 12), missing=[]),
        _sheet("wk2.png", range(12, 19), missing=[]),
        _sheet("wk3.png", range(19, 26), missing=[]),
        _sheet("wk4.png", range(26, 31), period_type="partial", missing=range(1, 26)),
    ]
    async with SessionLocal() as db:
        (g,) = await group_sheets(db, None, sheets)
    assert g["missing_days"] == []
    assert not any("missing day" in f for f in group_flags(g)), group_flags(g)
    assert len(g["working_days"]) == 26 and len(g["sheets"]) == 4


async def test_a_day_no_sheet_covers_is_still_reported_for_a_sheet_with_a_gap():
    from app.core.database import SessionLocal

    sheets = [_sheet("wk1.png", [7, 8, 10, 11], missing=[9]),
              _sheet("wk2.png", range(12, 19), missing=[])]
    async with SessionLocal() as db:
        (g,) = await group_sheets(db, None, sheets)
    assert g["missing_days"] == [9]
    assert any("missing day" in f for f in group_flags(g))


async def test_a_gap_that_another_sheet_fills_is_not_reported():
    from app.core.database import SessionLocal

    sheets = [_sheet("wk1.png", [7, 8, 10, 11], missing=[9]),
              _sheet("fill.png", [9], missing=[])]
    async with SessionLocal() as db:
        (g,) = await group_sheets(db, None, sheets)
    assert g["missing_days"] == []
