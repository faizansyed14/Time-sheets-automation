"""grouping.group_sheets — merging several sheets into one employee+month.

A weekly attendance sheet and a leave certificate for the SAME thread are
each internally conflict-free (normalise_sheet already guarantees that), but
nothing previously cross-checked between sheets once their buckets were
unioned — a leave certificate claiming "sick" on a day a weekly timesheet
claimed "working" reached auto-accept with a clean bill of health. Real
case this catches: 4 weekly attendance sheets for a month, then 2 leave
certificates (15 days each) covering the exact same days as sick/annual —
every one of those days ends up in BOTH working_days and a leave bucket.
"""
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.services.extract_email import auto_accept
from app.services.extract_email.grouping import group_sheets
from app.services.extract_email.constants import BUCKETS

_ALL_BUCKETS = BUCKETS


def _sheet(name: str, kind: str, month: int, year: int, **overrides) -> dict:
    base = {
        "name": name, "kind": kind,
        "employee_name": "Grouping Test Person", "employee_id": "E-GROUP-TEST-1",
        "month": month, "year": year, "days_covered": 0, "period_type": "partial",
        "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
        **{b: [] for b in _ALL_BUCKETS},
        "manager_signature": False, "approval_evidence": "", "text": "", "notes": "",
    }
    base.update(overrides)
    return base


def _week_sheet(name: str, start_day: int, month: int = 6, year: int = 2026) -> dict:
    days = [f"{year}-{month:02d}-{d:02d}" for d in range(start_day, start_day + 7)]
    return _sheet(name, "timesheet", month, year,
                  days_covered=7, period_type="week", working_days=days)


def _leave_sheet(name: str, start_day: int, end_day: int, bucket: str,
                  month: int = 6, year: int = 2026) -> dict:
    days = [f"{year}-{month:02d}-{d:02d}" for d in range(start_day, end_day + 1)]
    return _sheet(name, "leave_certificate", month, year,
                  days_covered=len(days), period_type="partial", **{bucket: days})


async def _employee(db) -> Employee:
    emp = (await db.execute(select(Employee).where(
        Employee.employee_id == "E-GROUP-TEST-1"))).scalar_one_or_none()
    if not emp:
        emp = Employee(employee_id="E-GROUP-TEST-1", name="Grouping Test Person",
                       location="DXB", account_manager="Test Manager")
        db.add(emp)
        await db.commit()
        await db.refresh(emp)
    return emp


def _fake_email():
    from app.models.email_message import EmailMessage
    return EmailMessage(
        provider_message_id="grouping-test-anchor", conversation_id="conv-grouping-test",
        sender_name="X", sender_email="nobody@nowhere.invalid",
        subject="grouping test", received_at=None, body_text="", attachments=[])


async def test_leave_certificate_contradicting_a_week_sheet_is_flagged():
    """4 weekly attendance sheets (June 1-28, all "working") then 2 leave
    certificates that retroactively claim the SAME days as sick/annual —
    every overlapping day must be caught, not silently unioned into both
    working_days and a leave bucket."""
    async with SessionLocal() as db:
        await _employee(db)
        sheets = [
            _week_sheet("week1.pdf", 1), _week_sheet("week2.pdf", 8),
            _week_sheet("week3.pdf", 15), _week_sheet("week4.pdf", 22),
            _leave_sheet("leave1.pdf", 1, 15, "sick"),
            _leave_sheet("leave2.pdf", 16, 30, "annual"),
        ]
        groups = await group_sheets(db, _fake_email(), sheets)
        assert len(groups) == 1
        g = groups[0]

        working = set(g["working_days"])
        sick = set(g["buckets"]["sick"])
        annual = set(g["buckets"]["annual"])
        # The union still keeps both sides (a human decides which is right) —
        # but every overlapping day must now show up as a flagged issue.
        assert working & sick == {f"2026-06-{d:02d}" for d in range(1, 16)}
        assert working & annual == {f"2026-06-{d:02d}" for d in range(16, 29)}

        conflict_dates = {
            i.split(":", 1)[1].strip().split(" in ", 1)[0]
            for i in g["issues"] if i.startswith("date in two categories across sheets")
        }
        assert conflict_dates == (working & sick) | (working & annual), g["issues"]

        # A contradiction this size must block auto-accept, not sail through.
        decision = auto_accept.evaluate(g)
        assert not decision.accepted
        assert any("validation flags" in b for b in decision.blockers)


async def test_non_overlapping_week_sheets_have_no_cross_sheet_conflict():
    """4 genuinely complementary weekly sheets (no leave, no overlap) must
    NOT be flagged — the fix must not create false positives for the
    ordinary partial-sheet-merge case this pipeline relies on everywhere
    else."""
    async with SessionLocal() as db:
        await _employee(db)
        sheets = [
            _week_sheet("week1.pdf", 1), _week_sheet("week2.pdf", 8),
            _week_sheet("week3.pdf", 15), _week_sheet("week4.pdf", 22),
        ]
        groups = await group_sheets(db, _fake_email(), sheets)
        assert len(groups) == 1
        assert groups[0]["issues"] == []


async def test_public_holiday_alongside_working_across_sheets_is_compatible():
    """A weekly timesheet says worked; a separate public-holiday circular
    says the same day was a public holiday — two true facts about one day,
    not a contradiction, exactly like the existing within-sheet exception."""
    async with SessionLocal() as db:
        await _employee(db)
        sheets = [
            _week_sheet("week1.pdf", 1),
            _leave_sheet("holiday-notice.pdf", 1, 1, "public_holiday"),
        ]
        groups = await group_sheets(db, _fake_email(), sheets)
        assert len(groups) == 1
        assert groups[0]["issues"] == []
        assert "2026-06-01" in groups[0]["working_days"]
        assert "2026-06-01" in groups[0]["buckets"]["public_holiday"]
