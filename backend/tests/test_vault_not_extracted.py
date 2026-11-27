"""Files put straight into the File Vault without being extracted.

The app records each one at the moment it enters the vault (no listing of the vault on
any page load). Until extracted the person shows "in vault, not extracted" - never
missing - on the Dashboard and Export; the record follows the file when it is moved or
deleted; and Extract replaces it with the real review item.
"""
from types import SimpleNamespace

from sqlalchemy import delete, select

from app.core import datacache
from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile
from app.models.timesheet_record import TimesheetRecord
from app.services import storage_provider as sp
from app.services.pipeline.vault_pending import PENDING_TAG
from tests.conftest import auth_headers

YEAR, MONTH = 2032, 3


async def _emp(client, h, eid, name, manager):
    r = await client.post("/api/v1/employee-matcher", headers=h, json={
        "employee_id": eid, "name": name, "account_manager": manager, "location": "DXB"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _pending(source_like="vault:%"):
    async with SessionLocal() as db:
        return (await db.execute(select(PipelineFile).where(
            PipelineFile.attachment_id == PENDING_TAG, PipelineFile.source_id.like(source_like)))).scalars().all()


def _local_vault(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "storage_provider", "local")
    sp.get_storage_provider.cache_clear()


async def _cleanup(pks):
    sp.get_storage_provider.cache_clear()
    async with SessionLocal() as db:
        await db.execute(delete(PipelineFile).where(PipelineFile.source_id.like("vault:%")))
        await db.execute(delete(TimesheetRecord).where(TimesheetRecord.matched_employee_pk.in_(pks)))
        await db.execute(delete(Employee).where(Employee.id.in_(pks)))
        await db.commit()
    await datacache.bust_employees()
    await datacache.bust_pipeline()


async def test_a_vault_upload_is_tracked_shown_and_follows_its_file(client, admin_token, tmp_path, monkeypatch):
    _local_vault(tmp_path, monkeypatch)
    h = auth_headers(admin_token)
    person = await _emp(client, h, "VLT-1", "Vault Person", "Vault Mgr")
    nobody = await _emp(client, h, "VLT-2", "Nothing Person", "Vault Mgr")
    try:
        mgr, folder = sp.employee_vault_location("Vault Mgr", "Vault Person", None, None)
        label = sp.month_label(MONTH, YEAR)
        up = await client.post(f"/api/v1/files/managers/{mgr}/employees/{folder}/months/{label}/files",
                               headers=h, files={"files": ("march.pdf", b"%PDF-1.4 x", "application/pdf")})
        assert up.status_code == 201, up.text
        rel = up.json()["saved"][0]["rel_path"]
        mine = f"vault:{mgr}/{folder}/%"
        items = await _pending(mine)
        assert [i.source_id for i in items] == [f"vault:{rel}"]
        assert items[0].extraction_meta["staged"]["employee_pk"] == person

        # Dashboard + Export: in vault, not extracted - never missing; nobody else affected
        cov = (await client.get("/api/v1/employees/coverage", headers=h,
                                params={"year": YEAR, "month": MONTH, "q": "Person"})).json()
        rows = {r["employee_name"]: r for r in cov["rows"]}
        assert rows["Vault Person"]["in_vault_this_month"] is True
        assert rows["Vault Person"]["awaiting_review_this_month"] is False
        assert rows["Nothing Person"]["in_vault_this_month"] is False
        missing = (await client.get("/api/v1/employees/coverage", headers=h, params={
            "year": YEAR, "month": MONTH, "status": "missing", "q": "Person"})).json()
        ours = {"Vault Person", "Nothing Person"}       # other tests leave employees behind
        assert [r["employee_name"] for r in missing["rows"] if r["employee_name"] in ours] == ["Nothing Person"]
        export = {r["employee_id"]: r["status"] for r in (await client.get(
            "/api/v1/timesheets/by-period", headers=h, params={"month": MONTH, "year": YEAR})).json()}
        assert export["VLT-1"] == "In Vault, Not Extracted" and export["VLT-2"] == "Not Received"

        # Moving the file moves its tracking; deleting the file drops it
        new_rel = f"{mgr}/{folder}/{sp.month_label(4, YEAR)}/march.pdf"
        mv = await client.post("/api/v1/files/move-path", headers=h,
                               json={"src_rel_path": rel, "dst_rel_path": new_rel, "as_copy": False})
        assert mv.status_code == 200, mv.text
        moved = mv.json()["rel_path"]
        items = await _pending(mine)
        assert [(i.source_id, i.month) for i in items] == [(f"vault:{moved}", 4)]
        assert (await client.delete("/api/v1/files/file", headers=h, params={"rel_path": moved})).status_code == 200
        assert await _pending(mine) == []
    finally:
        await _cleanup([person, nobody])


async def test_extract_replaces_the_pending_item(client, admin_token, tmp_path, monkeypatch):
    _local_vault(tmp_path, monkeypatch)
    h = auth_headers(admin_token)
    person = await _emp(client, h, "VLT-3", "Extract Person", "Vault Mgr")
    try:
        mgr, folder = sp.employee_vault_location("Vault Mgr", "Extract Person", None, None)
        label = sp.month_label(MONTH, YEAR)
        up = await client.post(f"/api/v1/files/managers/{mgr}/employees/{folder}/months/{label}/files",
                               headers=h, files={"files": ("s.pdf", b"%PDF-1.4 y", "application/pdf")})
        rel = up.json()["saved"][0]["rel_path"]
        from app.services.agents import full_email_extract as fx
        seen = {}

        async def fake_extract_upload(db, *, filename, content_type, data, source_id=None):
            seen.update(data=data, source_id=source_id)
            return {"staged": [SimpleNamespace(id="x")], "message": "ok"}
        monkeypatch.setattr(fx, "extract_upload", fake_extract_upload)
        r = await client.post("/api/v1/files/extract", headers=h, json={"rel_path": rel})
        assert r.status_code == 200 and r.json()["staged"] == 1
        assert seen == {"data": b"%PDF-1.4 y", "source_id": f"vault:{rel}"}
        assert await _pending(f"vault:{mgr}/{folder}/%") == [], "the review item replaces the 'not extracted' one"
    finally:
        await _cleanup([person])
