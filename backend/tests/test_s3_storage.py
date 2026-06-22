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
