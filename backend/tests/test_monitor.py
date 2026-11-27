"""Admin Monitor: metrics recorded by any process are read back as one snapshot."""
from tests.conftest import auth_headers


def test_recorded_llm_calls_show_up_in_the_snapshot():
    from app.services.monitoring import reader
    from app.services.monitoring.recorder import get_recorder, redis_client, role_from_label

    if redis_client() is None:
        import pytest
        pytest.skip("Redis not available")
    rec = get_recorder()
    rec.record("llm", role_from_label("pass2-batch3"), "test/model-x", 1200, tokens_in=1000,
               tokens_out=200, cost=0.0123, detail={"label": "pass2-batch3"})
    rec.record("llm", role_from_label("pass1-rescue-A4"), "test/model-x", 900, status="error",
               detail={"label": "pass1-rescue-A4", "error": "boom"})
    rec.flush()
    snap = reader.snapshot(15)
    rows = {(r["role"], r["model"]): r for r in snap["llm"]["by_step"]}
    assert rows[("pass2", "test/model-x")]["calls"] >= 1
    assert rows[("pass2", "test/model-x")]["cost_usd"] >= 0.0123
    assert rows[("pass1-rescue", "test/model-x")]["errors"] >= 1
    assert any(e.get("detail", {}).get("error") == "boom" for e in snap["failures"])


def test_role_from_label():
    from app.services.monitoring.recorder import role_from_label
    assert role_from_label("pass1-batch0") == "pass1"
    assert role_from_label("pass2-recover-A12") == "pass2-recover"
    assert role_from_label("alternates-A3") == "alternates"
    assert role_from_label("roster-census-p2") == "roster-census"
    assert role_from_label("") == "other"


async def test_monitor_endpoint_is_admin_only_and_returns_a_snapshot(client, admin_token):
    r = await client.get("/api/v1/admin/monitor", headers=auth_headers(admin_token), params={"minutes": 60})
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("llm", "graph", "jobs", "api", "processes", "queues", "redis", "disk", "sync", "auto_extract", "alerts"):
        assert key in body
    assert (await client.get("/api/v1/admin/monitor")).status_code in (401, 403)


def test_alerts_flag_missing_workers_and_sync_failure():
    from app.services.monitoring.reader import alerts
    snap = {"processes": [{"kind": "api", "online": True}],
            "llm": {"totals": {"calls": 10, "errors": 5}},
            "queues": {"available": True, "extract": 500}, "redis": {"available": True, "evicted_keys": 0,
                                                                      "used_mb": 1, "max_mb": 400},
            "disk": {"used_pct": 95, "free_gb": 1}}
    out = alerts(snap, sync={"sync_error": "401"}, auto={"failed": 0})
    texts = " ".join(a["text"] for a in out)
    assert out[0]["level"] == "critical"
    assert "extraction worker" in texts and "Mail sync is failing" in texts
    assert "5 of 10 LLM calls failed" in texts and "500 threads" in texts and "95.0%" in texts or "95%" in texts


async def test_old_debug_runs_are_purged_with_their_pictures(monkeypatch):
    import datetime as dt
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.extraction_debug_run import ExtractionDebugRun
    from app.services.extract_email.debug_capture import purge_old_debug_runs
    from app.services.pipeline import raw_store

    removed_prefixes = []
    monkeypatch.setattr(raw_store, "delete_prefix", lambda p: removed_prefixes.append(p) or 1)
    old_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    async with SessionLocal() as db:
        cols = {c.name for c in ExtractionDebugRun.__table__.columns}
        base = {k: v for k, v in {"source_kind": "email", "source_id": "dbg", "subject": "s"}.items() if k in cols}
        old = ExtractionDebugRun(**base)
        new = ExtractionDebugRun(**base)
        db.add_all([old, new])
        await db.flush()
        old.created_at = old_at
        await db.commit()
        old_id, new_id = old.id, new.id
    try:
        async with SessionLocal() as db:
            assert await purge_old_debug_runs(db, days=7) >= 1
            left = set((await db.execute(select(ExtractionDebugRun.id).where(
                ExtractionDebugRun.id.in_([old_id, new_id])))).scalars())
        assert left == {new_id}
        assert f"debug/{old_id}" in removed_prefixes and f"debug/{new_id}" not in removed_prefixes
    finally:
        from sqlalchemy import delete
        async with SessionLocal() as db:
            await db.execute(delete(ExtractionDebugRun).where(ExtractionDebugRun.id.in_([old_id, new_id])))
            await db.commit()
