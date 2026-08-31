"""POST /files/move-path — the Files page's Move/Copy action, exercised
through the real HTTP route (not just the storage provider directly)."""
from tests.conftest import auth_headers


async def test_move_route_relocates_a_file(client, admin_token):
    h = auth_headers(admin_token)
    mgr, emp, month = "Move Test Manager", "Move Test Employee", "June-2026"

    await client.post("/api/v1/files/managers", headers=h, json={"name": mgr})
    await client.post(f"/api/v1/files/managers/{mgr}/employees", headers=h, json={"name": emp})
    await client.post(f"/api/v1/files/managers/{mgr}/employees/{emp}/months", headers=h,
                      json={"month_label": month})
    up = await client.post(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/files",
        headers=h, files={"files": ("sheet.pdf", b"%PDF-1.4 hello", "application/pdf")})
    assert up.status_code == 201, up.text

    src = f"{mgr}/{emp}/{month}/sheet.pdf"
    dst = f"{mgr}/{emp}/July-2026/sheet.pdf"
    r = await client.post("/api/v1/files/move-path", headers=h,
                          json={"src_rel_path": src, "dst_rel_path": dst})
    assert r.status_code == 200, r.text
    assert r.json()["rel_path"] == dst

    old_items = await client.get(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/items", headers=h)
    assert old_items.json() == []
    new_items = await client.get(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/July-2026/items", headers=h)
    assert [i["name"] for i in new_items.json()] == ["sheet.pdf"]


async def test_copy_route_keeps_the_source(client, admin_token):
    h = auth_headers(admin_token)
    mgr, emp, month = "Copy Test Manager", "Copy Test Employee", "June-2026"

    await client.post("/api/v1/files/managers", headers=h, json={"name": mgr})
    await client.post(f"/api/v1/files/managers/{mgr}/employees", headers=h, json={"name": emp})
    await client.post(f"/api/v1/files/managers/{mgr}/employees/{emp}/months", headers=h,
                      json={"month_label": month})
    await client.post(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/files",
        headers=h, files={"files": ("sheet.pdf", b"%PDF-1.4 hello", "application/pdf")})

    src = f"{mgr}/{emp}/{month}/sheet.pdf"
    dst = f"{mgr}/{emp}/July-2026/sheet.pdf"
    r = await client.post("/api/v1/files/move-path", headers=h,
                          json={"src_rel_path": src, "dst_rel_path": dst, "as_copy": True})
    assert r.status_code == 200, r.text

    old_items = await client.get(
        f"/api/v1/files/managers/{mgr}/employees/{emp}/months/{month}/items", headers=h)
    assert [i["name"] for i in old_items.json()] == ["sheet.pdf"], "copy must not remove the source"


async def test_move_route_rejects_missing_source(client, admin_token):
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/files/move-path", headers=h, json={
        "src_rel_path": "Nobody/Nothing/June-2026/missing.pdf",
        "dst_rel_path": "Somebody/Something/June-2026/missing.pdf",
    })
    assert r.status_code == 404


async def test_move_route_requires_full_access_not_read_only(client, admin_token):
    """Same require_write gate as every other mutating /files/* route —
    a read-only viewer must be rejected."""
    h = auth_headers(admin_token)
    create = await client.post("/api/v1/admin/users", headers=h, json={
        "username": "vault-viewer", "password": "ViewerPass123!",
        "role": "viewer", "auth_mode": "captcha",
    })
    assert create.status_code == 201, create.text

    from tests.conftest import login_2fa
    viewer_token = await login_2fa(client, "vault-viewer", "ViewerPass123!")
    vh = auth_headers(viewer_token)

    r = await client.post("/api/v1/files/move-path", headers=vh, json={
        "src_rel_path": "A/B/June-2026/x.pdf", "dst_rel_path": "A/C/June-2026/x.pdf",
    })
    assert r.status_code == 403
