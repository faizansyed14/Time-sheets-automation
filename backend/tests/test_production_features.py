"""
Production-hardening features:
  - CAPTCHA issuance rate limiting (refresh/get-new throttle)
  - pipeline raw-copy retention purge (60-day cleanup)
  - File Vault: upload into a month folder, delete a file, streaming ZIP
    download (whole + scoped to a subtree)
"""
import io
import zipfile

from tests.conftest import auth_headers


# --------------------------------------------------------------- CAPTCHA limit
async def test_captcha_issuance_rate_limited(client):
    """After captcha_rate_max issues from one IP, the next /auth/captcha is 429."""
    from app.core.config import settings

    orig = settings.captcha_rate_max
    settings.captcha_rate_max = 3
    # Unique source IP so this test's sliding window is independent of the admin
    # logins other tests performed from the default client address.
    ip_headers = {"X-Forwarded-For": "203.0.113.77"}
    try:
        codes = []
        for _ in range(6):
            r = await client.get("/api/v1/auth/captcha", headers=ip_headers)
            codes.append(r.status_code)
        # first 3 issued, then throttled
        assert codes[:3] == [200, 200, 200], codes
        assert codes[3] == 429, codes
    finally:
        settings.captcha_rate_max = orig


# --------------------------------------------------------- raw retention purge
def test_pipeline_raw_purge_removes_only_old(tmp_path, monkeypatch):
    import os
    import time
    from app.services.pipeline import raw_store
    from app.core.config import settings

    # Point the raw store at a temp dir and force the local code path.
    monkeypatch.setattr(settings, "pipeline_raw_root", str(tmp_path))
    monkeypatch.setattr(raw_store, "_use_s3", lambda: False)

    old = tmp_path / "old-id"
    new = tmp_path / "new-id"
    old.mkdir(); (old / "f.pdf").write_bytes(b"x")
    new.mkdir(); (new / "f.pdf").write_bytes(b"y")
    # age the "old" folder to 90 days ago
    ninety = time.time() - 90 * 86400
    os.utime(old, (ninety, ninety))

    removed = raw_store.purge_old(max_age_days=60)
    assert removed == 1
    assert not old.exists()
    assert new.exists()


# ---------------------------------------------------------------- vault upload
async def test_vault_upload_download_delete(client, admin_token):
    h = auth_headers(admin_token)
    mgr, emp, month = "Acme", "Jane Doe", "May-2026"
    # create the folder chain
    await client.post("/api/v1/files/managers", headers=h, json={"name": mgr})
    await client.post(f"/api/v1/files/managers/{mgr}/employees", headers=h, json={"name": emp})
    await client.post(f"/api/v1/files/managers/{mgr}/employees/{emp}/months", headers=h,
                      json={"month_label": month})

    # upload a PDF straight into the month folder
    up = await client.post(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/files",
        headers=h, files={"files": ("manual.pdf", b"%PDF-1.4 hello", "application/pdf")})
    assert up.status_code == 201, up.text
    saved = up.json()["saved"][0]
    assert saved["name"] == "manual.pdf"
    rel = saved["rel_path"]

    # it shows up in the month listing
    items = await client.get(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/items", headers=h)
    assert any(i["name"] == "manual.pdf" for i in items.json())

    # scoped streaming ZIP download contains the file
    z = await client.get(f"/api/v1/files/download-zip?rel_path={mgr}/{emp}", headers=h)
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = zf.namelist()
    assert any(n.endswith("manual.pdf") for n in names), names

    # delete the file
    d = await client.delete("/api/v1/files/file", headers=h, params={"rel_path": rel})
    assert d.status_code == 200
    items2 = await client.get(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/items", headers=h)
    assert not any(i["name"] == "manual.pdf" for i in items2.json())


async def test_year_dropdown_size_and_scoped_download(client, admin_token):
    h = auth_headers(admin_token)
    mgr, emp = "YearCo", "Sam Lee"
    await client.post("/api/v1/files/managers", headers=h, json={"name": mgr})
    await client.post(f"/api/v1/files/managers/{mgr}/employees", headers=h, json={"name": emp})
    # two different years
    for ml, body in (("March-2025", b"%PDF-1.4 2025"), ("April-2026", b"%PDF-1.4 2026-aaa")):
        await client.post(f"/api/v1/files/managers/{mgr}/employees/{emp}/months", headers=h,
                          json={"month_label": ml})
        up = await client.post(
            f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{ml}/files",
            headers=h, files={"files": (f"{ml}.pdf", body, "application/pdf")})
        assert up.status_code == 201, up.text

    # years dropdown lists both years, newest first, with sizes
    years = (await client.get("/api/v1/files/years", headers=h)).json()
    by_year = {y["year"]: y for y in years}
    assert 2025 in by_year and 2026 in by_year
    assert by_year[2026]["bytes"] > 0

    # size endpoint reports just the 2026 scope
    size = (await client.get("/api/v1/files/download-size?year=2026", headers=h)).json()
    assert size["files"] >= 1 and size["bytes"] > 0

    # year-scoped download contains ONLY 2026 files
    z = await client.get("/api/v1/files/download-zip?year=2026", headers=h)
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = zf.namelist()
    assert names and all("-2026/" in n for n in names), names
    assert not any("-2025/" in n for n in names), names
