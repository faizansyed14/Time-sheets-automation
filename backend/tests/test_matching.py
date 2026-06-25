"""Strict ID+name matching, including real short-form name variants."""
import uuid
import pytest

from app.services.pipeline import matching as M


@pytest.fixture(scope="module", autouse=True)
async def _seed_matcher(_setup_db):
    """Seed matcher rows once for this module (idempotent)."""
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.employee import Employee
    rows = [
        ("MT1", "Abdul Syed Ghani", "DXB"),
        ("MT2", "Mohammed Ali", "DXB"),
        ("MT9", "Abdul Syed Ghani", "DXB"),
        ("MT9", "Sara Mohammed Khan", "AUH"),
    ]
    async with SessionLocal() as db:
        for eid, name, loc in rows:
            exists = (await db.execute(select(Employee).where(
                Employee.employee_id == eid, Employee.name == name))).scalar_one_or_none()
            if not exists:
                db.add(Employee(id=str(uuid.uuid4()), employee_id=eid, name=name, location=loc))
        await db.commit()
    yield


async def _match(i, n):
    from app.core.database import SessionLocal
    async with SessionLocal() as db:
        return await M.match_employee(db, i, n)


@pytest.mark.parametrize("eid,name,code,matched", [
    ("MT1", "Abdul Syed Ghani", "id_and_name", "Abdul Syed Ghani"),  # exact
    ("MT1", "Abdul Ghani", "id_and_name", "Abdul Syed Ghani"),        # dropped middle name
    ("MT1", "A. Ghani", "id_and_name", "Abdul Syed Ghani"),           # initial + surname
    ("MT1", "Abd Ghani", "id_and_name", "Abdul Syed Ghani"),          # prefix abbreviation
    ("MT2", "Mohd Ali", "id_and_name", "Mohammed Ali"),               # fuzzy abbreviation
    ("MT1", "Abdul Ali", "no_match", None),                           # first name only -> NOT matched
    ("MT2", "Zzz Wrong", "no_match", None),                           # wrong name
    ("MT5", "Abdul Ghani", "no_match", None),                         # id not in matcher
    ("MT9", "Sara Khan", "id_and_name", "Sara Mohammed Khan"),        # shared id, disambiguated
    ("MT9", "Unknown Person", "ambiguous_id", None),                  # shared id, no name agreement
])
async def test_strict_matching_variants(eid, name, code, matched):
    r = await _match(eid, name)
    assert r.code == code, (eid, name, r.code, r.note)
    assert (r.employee.name if r.employee else None) == matched


async def test_requires_both_id_and_name():
    assert (await _match("MT1", None)).employee is None        # id only
    assert (await _match(None, "Abdul Ghani")).employee is None  # name only
