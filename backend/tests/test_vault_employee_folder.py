"""The File Vault employee folder carries the person's ACO/DCO numbers.

A folder named by name alone can't tell two people apart and doesn't say which
contract the files belong to. The label is built at filing time from the
matcher record, and an employee whose numbers were added/corrected later has
their EXISTING folder renamed rather than being given a second one.
"""
import pytest

from app.services.storage_provider import (
    employee_folder_base,
    employee_folder_label,
    ensure_employee_folder,
    get_storage_provider,
)


def test_label_includes_whichever_numbers_exist():
    assert employee_folder_label("Jane Doe", "1", "2") == "Jane Doe (ACO-1, DCO-2)"
    assert employee_folder_label("Jane Doe", "1", None) == "Jane Doe (ACO-1)"
    assert employee_folder_label("Jane Doe", None, "2") == "Jane Doe (DCO-2)"
    # No numbers at all -> the bare name, i.e. exactly the pre-ACO/DCO layout,
    # so employees without them keep the folder they already have.
    assert employee_folder_label("Jane Doe", None, None) == "Jane Doe"
    assert employee_folder_label("Jane Doe", "  ", "") == "Jane Doe"


def test_base_recovers_the_plain_name():
    assert employee_folder_base("Jane Doe (ACO-1, DCO-2)") == "Jane Doe"
    assert employee_folder_base("Jane Doe (DCO-2)") == "Jane Doe"
    assert employee_folder_base("Jane Doe") == "Jane Doe"
    # A name that legitimately contains brackets isn't mangled.
    assert employee_folder_base("Jane Doe (Contractor)") == "Jane Doe (Contractor)"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services import storage_provider as sp

    monkeypatch.setattr(settings, "storage_provider", "local")
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    sp.get_storage_provider.cache_clear()
    yield get_storage_provider()
    sp.get_storage_provider.cache_clear()


def test_existing_folder_is_renamed_when_numbers_arrive(vault):
    """The employee was filed before their ACO/DCO was known. Filing them again
    afterwards must MOVE the old folder, not start a second one — otherwise
    their history is split across two folders."""
    vault.save_file("Acme", "Jane Doe", "June-2026", "sheet.pdf", b"%PDF-1.4 x")
    assert [e.name for e in vault.list_employees("Acme")] == ["Jane Doe"]

    folder = ensure_employee_folder("Acme", "Jane Doe", "1", "2")

    assert folder == "Jane Doe (ACO-1, DCO-2)"
    assert [e.name for e in vault.list_employees("Acme")] == ["Jane Doe (ACO-1, DCO-2)"]
    # the already-filed sheet moved with it
    items = vault.list_items("Acme", "Jane Doe (ACO-1, DCO-2)", "June-2026")
    assert [i.name for i in items] == ["sheet.pdf"]


def test_corrected_numbers_rename_rather_than_duplicate(vault):
    vault.save_file("Acme", "Jane Doe (ACO-1, DCO-2)", "June-2026", "sheet.pdf", b"x")

    folder = ensure_employee_folder("Acme", "Jane Doe", "9", "2")

    assert folder == "Jane Doe (ACO-9, DCO-2)"
    assert [e.name for e in vault.list_employees("Acme")] == ["Jane Doe (ACO-9, DCO-2)"]


def test_untouched_when_the_folder_already_matches(vault):
    vault.save_file("Acme", "Jane Doe (ACO-1, DCO-2)", "June-2026", "sheet.pdf", b"x")

    assert ensure_employee_folder("Acme", "Jane Doe", "1", "2") == "Jane Doe (ACO-1, DCO-2)"
    assert [e.name for e in vault.list_employees("Acme")] == ["Jane Doe (ACO-1, DCO-2)"]


@pytest.mark.asyncio
async def test_accept_files_under_aco_dco_folder(vault):
    """Compare & Fix Accept must create the vault folder WITH ACO/DCO — same
    label Save-to-Vault uses — never the bare employee name alone."""
    from app.core.database import SessionLocal
    from app.models.employee import Employee
    from app.models.pipeline_file import PipelineFile
    from app.models.timesheet_record import TimesheetRecord
    from app.services.pipeline.ingestion import ingest_manual_entry
    from sqlalchemy import delete, select

    async with SessionLocal() as db:
        emp = Employee(
            employee_id="E-VAULT-1", name="Vault Folder Person",
            account_manager="Acme", aco_number="11", dco_number="22",
        )
        db.add(emp)
        await db.commit()
        await db.refresh(emp)
        emp_id = emp.id
        try:
            rec, tracker = await ingest_manual_entry(
                db, employee_pk=emp_id, month=6, year=2026,
                buckets={"annual": ["2026-06-02"]},
                attachments=[("sheet.pdf", "application/pdf", b"%PDF-1.4 x")],
            )
            await db.commit()
            assert rec.storage_folder == "Acme/Vault Folder Person (ACO-11, DCO-22)/June-2026", (
                rec.storage_folder)
            names = [e.name for e in vault.list_employees("Acme")]
            assert "Vault Folder Person (ACO-11, DCO-22)" in names, names
            assert "Vault Folder Person" not in names
        finally:
            await db.execute(delete(PipelineFile).where(
                PipelineFile.source_id.like(f"manual:E-VAULT-1:%")))
            await db.execute(delete(TimesheetRecord).where(
                TimesheetRecord.matched_employee_pk == emp_id))
            await db.execute(delete(Employee).where(Employee.id == emp_id))
            await db.commit()


def test_ambiguous_match_never_renames(vault):
    """Two folders share a base name — renaming either could merge two
    different people's files, so nothing is touched and the new folder is
    simply used going forward."""
    vault.save_file("Acme", "Jane Doe", "June-2026", "a.pdf", b"x")
    vault.save_file("Acme", "Jane Doe (DCO-5)", "June-2026", "b.pdf", b"x")

    folder = ensure_employee_folder("Acme", "Jane Doe", "1", "2")

    assert folder == "Jane Doe (ACO-1, DCO-2)"
    names = sorted(e.name for e in vault.list_employees("Acme"))
    assert names == ["Jane Doe", "Jane Doe (DCO-5)"]


# ---------------------------------------------------------------------------
# Re-filing the SAME thread (same subject-derived filename) must never
# silently overwrite what's already in the vault — see _dedupe_filename.
# ---------------------------------------------------------------------------

def test_first_filing_keeps_the_original_name(vault):
    from app.services import storage_provider as sp

    rel = sp.save_file("Acme", "Jane Doe", 6, 2026, "thread.eml", b"first version")

    assert rel.endswith("thread.eml")
    items = vault.list_items("Acme", "Jane Doe", "June-2026")
    assert [i.name for i in items] == ["thread.eml"]


def test_refiling_the_same_name_adds_a_dated_copy_without_touching_the_original(vault):
    """A thread re-extracted after a new reply builds a fresh bundle under the
    SAME subject-derived filename as before. It must land as an ADDITIONAL
    file, not silently replace the one already there."""
    from app.services import storage_provider as sp

    sp.save_file("Acme", "Jane Doe", 6, 2026, "thread.eml", b"first version")
    sp.save_file("Acme", "Jane Doe", 6, 2026, "thread.eml", b"second version, new reply")

    items = {i.name for i in vault.list_items("Acme", "Jane Doe", "June-2026")}
    assert "thread.eml" in items, "the original must still be there, untouched"
    assert len(items) == 2, f"the update must land as a SEPARATE file, got {items}"

    original_bytes, _, _ = vault.read_file(f"Acme/Jane Doe/June-2026/thread.eml")
    assert original_bytes == b"first version", "re-filing must never touch the original bytes"

    new_name = next(n for n in items if n != "thread.eml")
    new_bytes, _, _ = vault.read_file(f"Acme/Jane Doe/June-2026/{new_name}")
    assert new_bytes == b"second version, new reply"


def test_a_third_filing_the_same_day_gets_its_own_distinct_name_too(vault):
    from app.services import storage_provider as sp

    sp.save_file("Acme", "Jane Doe", 6, 2026, "thread.eml", b"v1")
    sp.save_file("Acme", "Jane Doe", 6, 2026, "thread.eml", b"v2")
    sp.save_file("Acme", "Jane Doe", 6, 2026, "thread.eml", b"v3")

    items = [i.name for i in vault.list_items("Acme", "Jane Doe", "June-2026")]
    assert len(items) == 3, f"every re-filing must keep its own copy, got {items}"
    assert len(set(items)) == 3, "all three filenames must be distinct"
