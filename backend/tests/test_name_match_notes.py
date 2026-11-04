"""A match that rests on similar spelling alone is shown to the reviewer."""
from types import SimpleNamespace

from app.services.extract_email.grouping import group_sheets
from app.services.pipeline import matching
from app.services.pipeline.matching import MatchCode, MatchResult, name_match_strength


def test_name_match_strength():
    assert name_match_strength("BOURNE, DAVID", "David Bourne") == "exact"
    assert name_match_strength("Naomi Ramos", "Naomi Ramos Cruz") == "short-form"
    assert name_match_strength("Mohd Ali", "Mohammed Ali") == "exact"
    assert name_match_strength("Sara Khan", "Sana Khan") == "approximate"
    assert name_match_strength("Mohamed Ali", "Muhammad Ali") == "exact"          # known transliteration
    assert name_match_strength("Mohd Ahmad", "Mohammed Ahmed") == "exact"
    assert name_match_strength("Ahmed Hassan", "Ahmed Hussain") == "approximate"  # different names
    assert name_match_strength("Mahmoud Ali", "Mohammed Ali") == "approximate"


def _sheet(name, emp_id):
    return {"kind": "timesheet", "name": "w.pdf", "employee_name": name, "employee_id": emp_id,
            "month": 6, "year": 2026, "period_type": "week", "days_covered": 3, "missing_days": [],
            "working_days": ["2026-06-01", "2026-06-02", "2026-06-03"], "weekend_days": [],
            "uncertain_days": [], "annual": [], "remote": [], "sick": [], "maternity": [],
            "unpaid": [], "absent": [], "public_holiday": [], "other": [], "notes": ""}


async def _group(monkeypatch, sheet_name, sheet_id, emp_name, emp_id):
    from app.core.database import SessionLocal

    emp = SimpleNamespace(id="pk-1", name=emp_name, employee_id=emp_id)

    async def fake_match(db, eid, name, **kw):
        return MatchResult(emp, "Matched by name.", MatchCode.NAME_PRIMARY)

    monkeypatch.setattr(matching, "match_employee", fake_match)
    async with SessionLocal() as db:
        (g,) = await group_sheets(db, None, [_sheet(sheet_name, sheet_id)])
    return g


async def test_a_one_letter_different_name_carries_a_note(monkeypatch):
    g = await _group(monkeypatch, "Sara Khan", None, "Sana Khan", "E100")
    assert any("approximate match" in n and "Sana Khan" in n for n in g["fold_notes"]), g["fold_notes"]


async def test_exact_short_form_or_id_confirmed_matches_carry_no_note(monkeypatch):
    g = await _group(monkeypatch, "Khan, Sana", None, "Sana Khan", "E100")
    assert not any("approximate" in n for n in g["fold_notes"])
    g = await _group(monkeypatch, "Sara Khan", "E100", "Sana Khan", "E100")        # the printed ID agrees
    assert not any("approximate" in n for n in g["fold_notes"])
