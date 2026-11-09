"""A sheet with days in two calendar months is split so each month's record is
built from the days that belong to it (records are per employee + month).

Before: a weekly sheet for 29 Jun - 5 Jul stayed whole under ONE month; the other
month's days were kept but flagged "outside the month" and never booked to the
month they belong to.
"""
from app.services.extract_email.grouping import group_sheets, split_sheet_by_month


def _sheet(**over) -> dict:
    base = {
        "kind": "timesheet", "name": "w.pdf", "employee_name": None, "employee_id": None,
        "month": 6, "year": 2026, "period_type": "week", "days_covered": 0, "missing_days": [],
        "working_days": [], "weekend_days": [], "uncertain_days": [], "annual": [], "remote": [],
        "sick": [], "maternity": [], "unpaid": [], "absent": [], "public_holiday": [], "other": [],
        "notes": "",
    }
    base.update(over)
    return base


def test_normal_sheet_inside_its_month_is_returned_untouched():
    s = _sheet(working_days=["2026-06-01", "2026-06-02"], days_covered=2)
    assert split_sheet_by_month(s) == [s]
    assert split_sheet_by_month(s)[0] is s


def test_week_spanning_two_months_becomes_one_shard_per_month():
    s = _sheet(
        working_days=["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03"],
        weekend_days=["2026-07-04", "2026-07-05"], sick=["2026-07-02"], days_covered=7)
    june, july = split_sheet_by_month(s)
    assert (june["year"], june["month"]) == (2026, 6)
    assert (july["year"], july["month"]) == (2026, 7)
    assert june["working_days"] == ["2026-06-29", "2026-06-30"]
    assert july["working_days"] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert july["weekend_days"] == ["2026-07-04", "2026-07-05"]
    assert july["sick"] == ["2026-07-02"] and june["sick"] == []
    assert june["days_covered"] == 2 and july["days_covered"] == 6
    assert june["period_type"] == "week" and july["period_type"] == "partial"   # declared month keeps its type
    assert "Jun 2026 and Jul 2026" in july["notes"]
    # nothing lost, nothing duplicated
    all_days = [d for sh in (june, july) for b in ("working_days", "weekend_days") for d in sh[b]]
    assert len(all_days) == len(set(all_days)) == 7


def test_leave_certificate_without_a_month_is_stamped_or_split_from_its_own_dates():
    one_month = _sheet(kind="leave_certificate", month=None, year=None, period_type="partial",
                       sick=["2026-08-10", "2026-08-11"])
    (only,) = split_sheet_by_month(one_month)
    assert (only["year"], only["month"]) == (2026, 8)

    two_months = _sheet(kind="leave_certificate", month=None, year=None, period_type="partial",
                        sick=["2026-08-31", "2026-09-01", "2026-09-02"])
    aug, sep = split_sheet_by_month(two_months)
    assert (aug["month"], sep["month"]) == (8, 9)
    assert aug["sick"] == ["2026-08-31"] and sep["sick"] == ["2026-09-01", "2026-09-02"]


def test_uncertain_days_follow_their_own_month():
    s = _sheet(working_days=["2026-06-30", "2026-07-01"],
               uncertain_days=[{"date": "2026-07-01", "reason": "smudged"}], days_covered=2)
    june, july = split_sheet_by_month(s)
    assert june["uncertain_days"] == []
    assert july["uncertain_days"] == [{"date": "2026-07-01", "reason": "smudged"}]


async def test_group_sheets_builds_one_group_per_month_for_a_cross_month_week():
    from app.core.database import SessionLocal

    s = _sheet(employee_name="Cross Month Person", employee_id="E-XM-1",
               working_days=["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02"],
               weekend_days=["2026-07-04"], days_covered=5)
    async with SessionLocal() as db:
        groups = await group_sheets(db, None, [s])
    months = sorted((g["year"], g["month"]) for g in groups)
    assert months == [(2026, 6), (2026, 7)]
    by_month = {g["month"]: g for g in groups}
    assert by_month[6]["working_days"] == ["2026-06-29", "2026-06-30"]
    assert by_month[7]["working_days"] == ["2026-07-01", "2026-07-02"]
    assert by_month[7]["weekend_days"] == ["2026-07-04"]
    assert by_month[6]["tag"] != by_month[7]["tag"]
