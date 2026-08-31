"""StorageProvider.move_path — the Files page's Move/Copy action.

rename_folder can only rename in place (same parent); move_path is the one
primitive that can actually reparent a file or folder to a different
manager/employee/month, which is what "move this sheet to the right
employee" or "reassign this employee to another manager" needs.
"""
import pytest

from app.services.storage_provider.local_provider import LocalStorageProvider


@pytest.fixture
def prov(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setattr(type(LocalStorageProvider()), "root", property(lambda self: root))
    return LocalStorageProvider(), root


def _write(root, rel_path: str, content: bytes = b"data") -> None:
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def test_move_file_to_a_new_employee_and_month(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet.pdf", b"original bytes")

    new_rel = p.move_path("Alice/Bob/June-2026/sheet.pdf", "Alice/Carol/July-2026/sheet.pdf")

    assert new_rel == "Alice/Carol/July-2026/sheet.pdf"
    assert not (root / "Alice/Bob/June-2026/sheet.pdf").exists()
    assert (root / "Alice/Carol/July-2026/sheet.pdf").read_bytes() == b"original bytes"


def test_copy_file_leaves_source_in_place(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet.pdf", b"original bytes")

    new_rel = p.move_path("Alice/Bob/June-2026/sheet.pdf", "Alice/Carol/June-2026/sheet.pdf", copy=True)

    assert new_rel == "Alice/Carol/June-2026/sheet.pdf"
    assert (root / "Alice/Bob/June-2026/sheet.pdf").exists(), "copy must not remove the source"
    assert (root / "Alice/Carol/June-2026/sheet.pdf").read_bytes() == b"original bytes"


def test_file_destination_collision_is_deduped_not_overwritten(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet.pdf", b"incoming")
    _write(root, "Alice/Carol/June-2026/sheet.pdf", b"already there, must survive")

    new_rel = p.move_path("Alice/Bob/June-2026/sheet.pdf", "Alice/Carol/June-2026/sheet.pdf")

    assert new_rel == "Alice/Carol/June-2026/sheet (2).pdf"
    assert (root / "Alice/Carol/June-2026/sheet.pdf").read_bytes() == b"already there, must survive"
    assert (root / "Alice/Carol/June-2026/sheet (2).pdf").read_bytes() == b"incoming"


def test_move_a_whole_employee_folder_to_a_new_manager(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet1.pdf")
    _write(root, "Alice/Bob/July-2026/sheet2.pdf")

    new_rel = p.move_path("Alice/Bob", "Denise/Bob")

    assert new_rel == "Denise/Bob"
    assert not (root / "Alice/Bob").exists()
    assert (root / "Denise/Bob/June-2026/sheet1.pdf").exists()
    assert (root / "Denise/Bob/July-2026/sheet2.pdf").exists()


def test_copy_a_whole_folder_leaves_source_tree_intact(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet1.pdf", b"x")

    p.move_path("Alice/Bob", "Denise/Bob", copy=True)

    assert (root / "Alice/Bob/June-2026/sheet1.pdf").exists()
    assert (root / "Denise/Bob/June-2026/sheet1.pdf").exists()


def test_folder_destination_that_already_exists_is_rejected(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet.pdf")
    _write(root, "Denise/Bob/May-2026/other.pdf")

    with pytest.raises(FileExistsError):
        p.move_path("Alice/Bob", "Denise/Bob")

    # Nothing should have moved.
    assert (root / "Alice/Bob/June-2026/sheet.pdf").exists()


def test_moving_a_folder_into_its_own_subtree_is_rejected(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet.pdf")

    with pytest.raises(ValueError):
        p.move_path("Alice/Bob", "Alice/Bob/June-2026")


def test_same_source_and_destination_is_a_noop_error(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet.pdf")

    with pytest.raises(ValueError):
        p.move_path("Alice/Bob/June-2026/sheet.pdf", "Alice/Bob/June-2026/sheet.pdf")


def test_missing_source_raises_file_not_found(prov):
    p, root = prov
    with pytest.raises(FileNotFoundError):
        p.move_path("Alice/Bob/June-2026/nope.pdf", "Alice/Carol/June-2026/nope.pdf")


def test_destination_escaping_the_storage_root_is_rejected(prov):
    p, root = prov
    _write(root, "Alice/Bob/June-2026/sheet.pdf")

    with pytest.raises(ValueError):
        p.move_path("Alice/Bob/June-2026/sheet.pdf", "../outside.pdf")
