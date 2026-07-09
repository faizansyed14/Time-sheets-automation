"""Local storage provider must confine every path to the storage root.

Regression for the prefix-check bypass: a sibling directory that merely shares
the root's name prefix (e.g. root '/app/storage' vs '/app/storage_backup')
used to slip through str().startswith(). Real vault paths never contain '..'
and must keep working."""
import pytest

from app.services.storage_provider.local_provider import LocalStorageProvider


@pytest.fixture
def prov(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    root.mkdir()
    sib = tmp_path / "storage_backup"
    sib.mkdir()
    (sib / "secret.txt").write_text("top secret")
    # Force the provider's root to our temp dir.
    monkeypatch.setattr(type(LocalStorageProvider()), "root",
                        property(lambda self: root))
    return LocalStorageProvider(), root, tmp_path


def test_sibling_prefix_dir_is_rejected(prov):
    p, root, tmp = prov
    with pytest.raises(ValueError):
        p._abs("../storage_backup/secret.txt")


def test_parent_traversal_is_rejected(prov):
    p, root, tmp = prov
    with pytest.raises(ValueError):
        p._abs("../../etc/passwd")


def test_legitimate_vault_path_is_allowed(prov):
    p, root, tmp = prov
    resolved = p._abs("Manager/Employee/June-2026/sheet.pdf")
    assert str(resolved).startswith(str(root.resolve()))


def test_root_itself_is_allowed(prov):
    p, root, tmp = prov
    assert p._abs("") == root.resolve()
