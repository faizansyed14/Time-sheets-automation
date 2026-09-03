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


async def test_prefetched_all_employees_matches_the_default_db_fetch():
    """The perf optimization (bulk roster staging passes a pre-fetched
    employee list through instead of re-querying per row — see
    roster_stage.build_groups) must never change WHICH employee is matched;
    it only removes redundant re-fetches of the same, unchanging table."""
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.employee import Employee

    async with SessionLocal() as db:
        all_employees = (await db.execute(select(Employee))).scalars().all()
        default = await M.match_employee(db, "MT1", "Abdul Ghani")
        prefetched = await M.match_employee(db, "MT1", "Abdul Ghani", all_employees=all_employees)
        assert prefetched.code == default.code
        default_id = default.employee.id if default.employee else None
        prefetched_id = prefetched.employee.id if prefetched.employee else None
        assert prefetched_id == default_id
    r = await _match(None, "Mohammed Ali")
    assert r.employee is not None and r.employee.name == "Mohammed Ali"


@pytest.mark.parametrize("eid,name,code,matched", [
    # id not in the matcher at all, but the name uniquely resolves -> name first
    ("MT404", "Mohammed Ali", "name_primary", "Mohammed Ali"),
    # id resolves to someone else, but the name uniquely matches a different
    # employee -> name first (client ID ignored)
    ("MT1", "Mohammed Ali", "name_primary", "Mohammed Ali"),
    # id not in the matcher and the name is ambiguous (two "Abdul Syed Ghani"
    # rows under different ids) -> still unmatched, never guesses
    ("MT404", "Abdul Syed Ghani", "no_match", None),
])
async def test_name_fallback_when_id_fails(eid, name, code, matched):
    r = await _match(eid, name)
    assert r.code == code, (eid, name, r.code, r.note)
    assert (r.employee.name if r.employee else None) == matched


async def test_deactivated_ghost_row_never_competes_with_the_real_employee():
    """A deactivated employee's row is kept forever (their vault history /
    filed records still point at it — see Employee.active's own docstring),
    but it must never be a MATCH CANDIDATE for a brand new sheet — otherwise a
    same-named ghost row silently steals (or ties with, forcing "ambiguous")
    a sheet that should go to the real, active person. This is the exact bug
    class already seen in production (duplicate "Priya Dey" rows)."""
    import uuid
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.employee import Employee

    async with SessionLocal() as db:
        real = Employee(id=str(uuid.uuid4()), employee_id="MT-GHOST-REAL",
                         name="Priya Dey", location="DXB", active=True)
        ghost = Employee(id=str(uuid.uuid4()), employee_id="MT-GHOST-OLD",
                          name="Priya Dey", location="DXB", active=False)
        db.add_all([real, ghost])
        await db.commit()

    try:
        # Name-only path: with the ghost excluded, "Priya Dey" resolves
        # uniquely to the real row instead of tying between two candidates
        # (which would otherwise return no_match — see _match_by_name).
        r = await _match(None, "Priya Dey")
        assert r.employee is not None and r.employee.id == real.id

        # Name still resolves first even when the client ID on the sheet
        # happens to be the now-inactive ghost's old id — matching is
        # name-primary by design (client Emp No is not the matcher's id), so
        # this correctly lands on the real employee, not the ghost, and not
        # a "no_match" either.
        r2 = await _match("MT-GHOST-OLD", "Priya Dey")
        assert r2.employee is not None and r2.employee.id == real.id

        # Force the ID fallback path specifically: a name that does NOT
        # agree with "Priya Dey" (so _match_by_name finds nothing) plus the
        # ghost's own id — _match_by_id's candidate query must now exclude
        # the inactive ghost, so this correctly falls through to no_match
        # rather than resolving to the deactivated row.
        r2b = await _match("MT-GHOST-OLD", "Someone Else Entirely")
        assert r2b.employee is None
        assert r2b.code == M.MatchCode.NO_MATCH

        # Pre-fetched-list callers (bulk roster staging, rematch, bulk
        # upload) must filter the same way at the call site — confirmed here
        # by mirroring what those call sites now do before passing the list in.
        async with SessionLocal() as db:
            active_only = (await db.execute(
                select(Employee).where(Employee.active.is_(True))
            )).scalars().all()
            r3 = await M.match_employee(db, None, "Priya Dey", all_employees=active_only)
            assert r3.employee is not None and r3.employee.id == real.id
    finally:
        async with SessionLocal() as db:
            for pk in (real.id, ghost.id):
                obj = await db.get(Employee, pk)
                if obj:
                    await db.delete(obj)
            await db.commit()
