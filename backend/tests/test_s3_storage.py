"""
S3 storage provider — exercised end-to-end against a mocked S3 (moto), so the
same code that talks to AWS in production is verified here without AWS.
"""
import pytest

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402


@mock_aws
def test_s3_provider_full_lifecycle(monkeypatch):
    import boto3

    from app.core.config import settings

    # point the provider at a mocked bucket
    monkeypatch.setattr(settings, "storage_provider", "s3")
    monkeypatch.setattr(settings, "s3_bucket", "ts-test-bucket")
    monkeypatch.setattr(settings, "s3_prefix", "timesheets")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "aws_access_key_id", "test")
    monkeypatch.setattr(settings, "aws_secret_access_key", "test")
    monkeypatch.setattr(settings, "s3_endpoint_url", None)

    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="ts-test-bucket")

    from app.services.storage_provider.s3_provider import S3StorageProvider
    sp = S3StorageProvider()

    # save two files under Manager/Employee/Month
    sp.save_file("Sarah Khan", "Mohammed Ali", "March-2026", "sheet.pdf", b"%PDF-1.4 data")
    sp.save_text("Sarah Khan", "Mohammed Ali", "March-2026", "result.json", '{"ok":true}')

    # 3-level listing reflects the keys
    managers = [m.name for m in sp.list_managers()]
    assert "Sarah Khan" in managers
    emps = [e.name for e in sp.list_employees("Sarah Khan")]
    assert "Mohammed Ali" in emps
    months = sp.list_months("Sarah Khan", "Mohammed Ali")
    assert months and months[0].name == "March-2026"
    items = {i.name for i in sp.list_items("Sarah Khan", "Mohammed Ali", "March-2026")}
    assert items == {"sheet.pdf", "result.json"}

    # read back
    data, name, _ctype = sp.read_file("Sarah Khan/Mohammed Ali/March-2026/sheet.pdf")
    assert data == b"%PDF-1.4 data" and name == "sheet.pdf"

    # build_zip streams from S3 via the active provider — clear the factory
    # cache so it rebuilds an S3 provider from the (monkeypatched) settings.
    import app.services.storage_provider as sp_pkg
    sp_pkg.get_storage_provider.cache_clear()
    try:
        from app.services.storage_provider.archive import build_zip
        import io, zipfile
        zf = zipfile.ZipFile(io.BytesIO(build_zip()))
        assert "Sarah Khan/Mohammed Ali/March-2026/sheet.pdf" in zf.namelist()
    finally:
        sp_pkg.get_storage_provider.cache_clear()

    # delete the month folder
    sp.delete_folder("Sarah Khan/Mohammed Ali/March-2026")
    assert sp.list_items("Sarah Khan", "Mohammed Ali", "March-2026") == []


@mock_aws
def test_s3_move_path_file_and_folder(monkeypatch):
    """move_path on S3 — the Files page's Move/Copy action. S3 has no real
    directories, so a "folder" move/copy is really a copy+delete loop over
    every key under that prefix (see S3StorageProvider.move_path)."""
    import boto3

    from app.core.config import settings

    monkeypatch.setattr(settings, "storage_provider", "s3")
    monkeypatch.setattr(settings, "s3_bucket", "ts-test-bucket-2")
    monkeypatch.setattr(settings, "s3_prefix", "timesheets")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "aws_access_key_id", "test")
    monkeypatch.setattr(settings, "aws_secret_access_key", "test")
    monkeypatch.setattr(settings, "s3_endpoint_url", None)

    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="ts-test-bucket-2")

    from app.services.storage_provider.s3_provider import S3StorageProvider
    sp = S3StorageProvider()

    # ---- move a single file ----
    sp.save_file("Alice", "Bob", "June-2026", "sheet.pdf", b"original bytes")
    new_rel = sp.move_path("Alice/Bob/June-2026/sheet.pdf", "Alice/Carol/July-2026/sheet.pdf")
    assert new_rel == "Alice/Carol/July-2026/sheet.pdf"
    data, _name, _ctype = sp.read_file("Alice/Carol/July-2026/sheet.pdf")
    assert data == b"original bytes"
    assert sp.list_items("Alice", "Bob", "June-2026") == []

    # ---- copy leaves the source in place ----
    sp.save_file("Alice", "Bob", "June-2026", "sheet2.pdf", b"copy me")
    copied_rel = sp.move_path("Alice/Bob/June-2026/sheet2.pdf", "Alice/Denise/June-2026/sheet2.pdf", copy=True)
    assert copied_rel == "Alice/Denise/June-2026/sheet2.pdf"
    assert {i.name for i in sp.list_items("Alice", "Bob", "June-2026")} == {"sheet2.pdf"}
    assert {i.name for i in sp.list_items("Alice", "Denise", "June-2026")} == {"sheet2.pdf"}

    # ---- file destination collision is deduped ----
    sp.save_file("Alice", "Bob", "June-2026", "sheet3.pdf", b"incoming")
    sp.save_file("Alice", "Denise", "June-2026", "sheet3.pdf", b"already there")
    deduped_rel = sp.move_path("Alice/Bob/June-2026/sheet3.pdf", "Alice/Denise/June-2026/sheet3.pdf")
    assert deduped_rel == "Alice/Denise/June-2026/sheet3 (2).pdf"
    kept, _n, _c = sp.read_file("Alice/Denise/June-2026/sheet3.pdf")
    moved, _n2, _c2 = sp.read_file("Alice/Denise/June-2026/sheet3 (2).pdf")
    assert kept == b"already there" and moved == b"incoming"

    # ---- move a whole employee folder to a different manager ----
    sp.save_file("Erin", "Frank", "May-2026", "a.pdf", b"a")
    sp.save_file("Erin", "Frank", "June-2026", "b.pdf", b"b")
    new_folder_rel = sp.move_path("Erin/Frank", "Grace/Frank")
    assert new_folder_rel == "Grace/Frank"
    assert sp.list_months("Erin", "Frank") == []
    months = {m.name for m in sp.list_months("Grace", "Frank")}
    assert months == {"May-2026", "June-2026"}

    # ---- folder destination that already exists is rejected ----
    sp.save_file("Henry", "Ivan", "May-2026", "x.pdf", b"x")
    sp.save_file("Jack", "Ivan", "May-2026", "y.pdf", b"y")
    with pytest.raises(FileExistsError):
        sp.move_path("Henry/Ivan", "Jack/Ivan")

    # ---- missing source ----
    with pytest.raises(FileNotFoundError):
        sp.move_path("Nobody/Nothing/June-2026/missing.pdf", "Alice/Bob/June-2026/missing.pdf")


def test_factory_selects_s3(monkeypatch):
    from app.core.config import settings
    import app.services.storage_provider as sp_pkg

    monkeypatch.setattr(settings, "storage_provider", "s3")
    monkeypatch.setattr(settings, "s3_bucket", "b")
    sp_pkg.get_storage_provider.cache_clear()
    try:
        provider = sp_pkg.get_storage_provider()
        assert provider.__class__.__name__ == "S3StorageProvider"
    finally:
        sp_pkg.get_storage_provider.cache_clear()
