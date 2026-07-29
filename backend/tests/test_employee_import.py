"""Employee matcher Excel import — additive by design.

The matcher is the source of truth for who exists, so an import must never
silently destroy data: it adds new people, updates changed fields on existing
ones, and NEVER deletes or blanks anything. `build_import_plan` is the dry run
the UI shows for confirmation before any of that is committed.
"""
from io import BytesIO

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.services.employee.import_service import (
    build_import_plan,
    import_employees_from_bytes,
)

DXB_HEADERS = ["Emp ID", "DCO", "Employees Name", "Project",
               "Account Managers Name", "Contact No.", "Email"]


def _xlsx(rows: list[list], headers: list[str] = DXB_HEADERS, title: str = "DXB") -> bytes:
    """A minimal in-memory workbook shaped like the real DXB sheet."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _row(emp_id, name, *, dco="D1", project="Proj", manager="Mgr",
         contact="050", email="a@x.ae") -> list:
    return [emp_id, dco, name, project, manager, contact, email]


@pytest.fixture
async def clean_matcher():
    """Isolate from the seeded matcher — this suite asserts on exact counts."""
    async with SessionLocal() as db:
        await db.execute(delete(Employee))
        await db.commit()
    yield
    async with SessionLocal() as db:
        await db.execute(delete(Employee))
        await db.commit()


async def _load(db) -> dict[str, Employee]:
    rows = (await db.execute(select(Employee))).scalars().all()
    return {e.employee_id: e for e in rows}


async def test_plan_reports_adds_updates_unchanged_and_leavers(clean_matcher):
    async with SessionLocal() as db:
        await import_employees_from_bytes(db, _xlsx([
            _row("E1", "Alice One", email="alice@x.ae", contact="0500000001"),
            _row("E2", "Bob Two", email="bob@x.ae"),
            _row("E3", "Carol Three", email="carol@x.ae"),
        ]))

    # Second sheet: E1 changed contact details, E2 identical, E3 gone, E4 new.
    second = _xlsx([
        _row("E1", "Alice One", email="alice.new@x.ae", contact="0509999999"),
        _row("E2", "Bob Two", email="bob@x.ae"),
        _row("E4", "Dave Four", email="dave@x.ae"),
    ])

    async with SessionLocal() as db:
        plan = await build_import_plan(db, second)

    assert [r["employee_id"] for r in plan["to_add"]] == ["E4"]

    assert len(plan["to_update"]) == 1
    upd = plan["to_update"][0]
    assert upd["employee_id"] == "E1"
    changed = {c["field"]: (c["old"], c["new"]) for c in upd["changes"]}
    assert changed["employee_email_id"] == ("alice@x.ae", "alice.new@x.ae")
    assert changed["contact_no"] == ("0500000001", "0509999999")

    assert [r["employee_id"] for r in plan["unchanged"]] == ["E2"]
    # E3 is absent from the new sheet — reported as a possible leaver...
    assert [r["employee_id"] for r in plan["missing_from_file"]] == ["E3"]

    # ...and the dry run wrote nothing at all.
    async with SessionLocal() as db:
        assert set((await _load(db)).keys()) == {"E1", "E2", "E3"}


async def test_import_adds_and_updates_but_never_deletes(clean_matcher):
    async with SessionLocal() as db:
        await import_employees_from_bytes(db, _xlsx([
            _row("E1", "Alice One", email="alice@x.ae"),
            _row("E3", "Carol Three", email="carol@x.ae"),
        ]))

    async with SessionLocal() as db:
        summary = await import_employees_from_bytes(db, _xlsx([
            _row("E1", "Alice One", email="alice.new@x.ae"),
            _row("E4", "Dave Four", email="dave@x.ae"),
        ]))

    assert summary["inserted"] == 1      # E4
    assert summary["updated"] == 1       # E1's email

    async with SessionLocal() as db:
        by_id = await _load(db)
    # E3 was NOT in the second file and is still here, untouched.
    assert set(by_id) == {"E1", "E3", "E4"}
    assert by_id["E3"].name == "Carol Three"
    assert by_id["E1"].employee_email_id == "alice.new@x.ae"


async def test_blank_cell_never_erases_a_stored_value(clean_matcher):
    async with SessionLocal() as db:
        await import_employees_from_bytes(db, _xlsx([
            _row("E1", "Alice One", email="alice@x.ae", contact="0500000001", project="Alpha"),
        ]))

    # A partial sheet: same person, but project/contact columns left empty.
    async with SessionLocal() as db:
        plan = await build_import_plan(db, _xlsx([
            _row("E1", "Alice One", email="alice@x.ae", contact="", project=""),
        ]))
        assert plan["to_update"] == []          # nothing to change
        assert len(plan["unchanged"]) == 1

        await import_employees_from_bytes(db, _xlsx([
            _row("E1", "Alice One", email="alice@x.ae", contact="", project=""),
        ]))

    async with SessionLocal() as db:
        e = (await _load(db))["E1"]
    assert e.project == "Alpha"
    assert e.contact_no == "0500000001"


async def test_import_does_not_reactivate_a_deactivated_employee(clean_matcher):
    async with SessionLocal() as db:
        await import_employees_from_bytes(db, _xlsx([_row("E1", "Alice One")]))
        e = (await _load(db))["E1"]
        e.active = False
        await db.commit()

    async with SessionLocal() as db:
        await import_employees_from_bytes(db, _xlsx([
            _row("E1", "Alice One", email="changed@x.ae"),
        ]))

    async with SessionLocal() as db:
        e = (await _load(db))["E1"]
    # Marking someone inactive is a deliberate admin decision — a bulk upload
    # updates their details but must not quietly bring them back.
    assert e.active is False
    assert e.employee_email_id == "changed@x.ae"


async def test_renamed_employee_is_flagged_not_silently_merged(clean_matcher):
    async with SessionLocal() as db:
        await import_employees_from_bytes(db, _xlsx([_row("E1", "Alice One")]))

    async with SessionLocal() as db:
        plan = await build_import_plan(db, _xlsx([_row("E1", "Alice One Smith")]))

    # Identity is ID + name, so this reads as a new person — but same ID in the
    # same office is surfaced as a likely rename for a human to resolve, never
    # auto-merged (AUH/DXB reuse ID ranges, so ID alone can't prove identity).
    assert len(plan["to_add"]) == 1
    assert plan["to_add"][0]["possible_rename_of"] == "Alice One (E1)"
    assert [r["employee_id"] for r in plan["missing_from_file"]] == ["E1"]


async def test_aco_and_dco_share_one_column_and_split_by_prefix(clean_matcher):
    """The real sheet keeps BOTH kinds in the single "DCO" column, telling them
    apart only by the prefix, with the employee's name appended:
        DCO2409846_YASIN WARAK        -> dco 2409846
        ACO1808209_MOHAMED ABDULLAH   -> aco 1808209
    The trailing name is dropped (the vault folder already leads with it)."""
    data = _xlsx([
        _row("E1", "Yasin Warak", dco="DCO2409846_YASIN WARAK"),
        _row("E2", "Mohamed Abdullah", dco="ACO1808209_MOHAMED ABDULLAH ELIMAM MOHAMED"),
        _row("E3", "Nakul Berry", dco="ACO1901597_NAKUL BERRY"),
        _row("E4", "Bare Number", dco="4471"),          # no prefix -> DCO
        _row("E5", "No Ref", dco="N/A"),
    ])
    async with SessionLocal() as db:
        plan = await build_import_plan(db, data)
        by_id = {r["employee_id"]: r for r in plan["to_add"]}
        assert (by_id["E1"]["aco_number"], by_id["E1"]["dco_number"]) == (None, "2409846")
        assert (by_id["E2"]["aco_number"], by_id["E2"]["dco_number"]) == ("1808209", None)
        assert (by_id["E3"]["aco_number"], by_id["E3"]["dco_number"]) == ("1901597", None)
        assert (by_id["E4"]["aco_number"], by_id["E4"]["dco_number"]) == (None, "4471")
        assert (by_id["E5"]["aco_number"], by_id["E5"]["dco_number"]) == (None, None)

        await import_employees_from_bytes(db, data)

    async with SessionLocal() as db:
        rows = await _load(db)
    assert rows["E1"].dco_number == "2409846" and rows["E1"].aco_number is None
    assert rows["E2"].aco_number == "1808209" and rows["E2"].dco_number is None


async def test_a_reference_that_switches_prefix_clears_the_other_field(clean_matcher):
    """Regression: the first import stored every value as a DCO (including the
    ACO ones). Re-importing must move it to aco_number AND clear the stale
    dco_number — the blank-cell rule would otherwise leave both set and the
    vault folder would claim two contract numbers for one person."""
    async with SessionLocal() as db:
        await import_employees_from_bytes(db, _xlsx([
            _row("E1", "Nakul Berry", dco="DCO1901597_NAKUL BERRY"),
        ]))
    async with SessionLocal() as db:
        assert (await _load(db))["E1"].dco_number == "1901597"

    async with SessionLocal() as db:
        plan = await build_import_plan(db, _xlsx([
            _row("E1", "Nakul Berry", dco="ACO1901597_NAKUL BERRY"),
        ]))
        changed = {c["field"]: (c["old"], c["new"]) for c in plan["to_update"][0]["changes"]}
        assert changed["aco_number"] == (None, "1901597")
        assert changed["dco_number"] == ("1901597", None)

        await import_employees_from_bytes(db, _xlsx([
            _row("E1", "Nakul Berry", dco="ACO1901597_NAKUL BERRY"),
        ]))

    async with SessionLocal() as db:
        e = (await _load(db))["E1"]
    assert e.aco_number == "1901597"
    assert e.dco_number is None


async def test_duplicate_rows_inside_the_file_are_skipped(clean_matcher):
    data = _xlsx([
        _row("E1", "Alice One"),
        _row("E1", "Alice One"),      # exact duplicate in the same file
        _row("", "No Id Person"),     # unusable
    ])
    async with SessionLocal() as db:
        plan = await build_import_plan(db, data)

    assert len(plan["to_add"]) == 1
    reasons = sorted(r["reason"] for r in plan["skipped"])
    assert reasons == ["Duplicate ID + Name in file", "Missing ID or Name"]
