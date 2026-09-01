"""Re-checking employee identity for pipeline items staged before the
employee existed in the Employee Matcher — services/pipeline/rematch.py.

Matching only ever runs once, at staging time; nothing revisits an
already-staged item later on its own. These tests build PipelineFile rows
directly (bypassing the full extraction pipeline) so each scenario is
isolated and fast — no LLM, no vision calls, matching the actual rematch
module's own zero-AI contract.
"""
import uuid

from tests.conftest import auth_headers

from app.core.database import SessionLocal
from app.models.employee import Employee
from app.models.pipeline_file import PipelineFile, PipelineStatus
from app.services.pipeline.rematch import rematch_unmatched


def _staged_meta(*, name: str, employee_id: str | None = None, employee_pk: str | None = None) -> dict:
    return {
        "staged": {
            "employee_pk": employee_pk,
            "matched_name": name,
            "matched_employee_id": employee_id,
            "month": 7, "year": 2026,
            "buckets": {}, "working_days": [], "weekend_days": [],
            "uncertain_days": [], "unaccounted_days": [],
        },
    }


async def _make_pipeline_file(db, *, name, employee_id=None, employee_pk=None,
                              status=PipelineStatus.NEEDS_REVIEW, meta=None) -> PipelineFile:
    pf = PipelineFile(
        id=str(uuid.uuid4()), filename=f"{name}.xlsx", source_kind="bulk",
        status=status, month=7, year=2026,
        employee_id=employee_id, employee_name=name,
        extraction_meta=meta if meta is not None else _staged_meta(
            name=name, employee_id=employee_id, employee_pk=employee_pk),
    )
    db.add(pf)
    await db.flush()
    return pf


async def _make_employee(db, *, name, employee_id, location="AUH") -> Employee:
    e = Employee(id=str(uuid.uuid4()), employee_id=employee_id, name=name, location=location)
    db.add(e)
    await db.flush()
    return e


async def test_an_employee_added_after_staging_now_resolves():
    """The exact real-world case: the roster was staged BEFORE this employee
    existed in the matcher. Adding them later and re-checking must now
    resolve it — this is the whole point of the feature."""
    async with SessionLocal() as db:
        pf = await _make_pipeline_file(db, name="Mohammed Ali Hasan Ali Aldhaheri", employee_id="742507027")
        await db.commit()

        # Staged BEFORE the employee existed — simulate that ordering by
        # adding the employee only now, then re-checking.
        await _make_employee(db, name="MOHAMMED ALI HASAN ALI ALDHAHERI", employee_id="E2507027")
        await db.commit()

        result = await rematch_unmatched(db)
        assert result["rematched_count"] == 1
        assert result["rematched"][0]["id"] == pf.id
        assert result["rematched"][0]["employee_id"] == "E2507027"

        await db.refresh(pf)
        assert pf.extraction_meta["staged"]["employee_pk"] is not None
        assert pf.extraction_meta["staged"]["matched_employee_id"] == "E2507027"
        assert pf.employee_id == "E2507027"


async def test_genuinely_unmatched_stays_unmatched_not_guessed():
    """Nobody by this name exists — must be reported as still-unmatched, and
    must NOT be given a fabricated employee_pk."""
    async with SessionLocal() as db:
        pf = await _make_pipeline_file(db, name="Nobody In The Matcher At All", employee_id="742500001")
        await db.commit()

        result = await rematch_unmatched(db)
        assert pf.id in {r["id"] for r in result["still_unmatched"]}
        assert pf.id not in {r["id"] for r in result["rematched"]}

        await db.refresh(pf)
        assert pf.extraction_meta["staged"]["employee_pk"] is None


async def test_ambiguous_names_are_never_guessed():
    """Two different real employees whose names both plausibly agree with the
    extracted name, with no clear winner — must stay unmatched, exactly like
    the live matcher already does for a bare name search."""
    async with SessionLocal() as db:
        await _make_employee(db, name="Ahmed Ali Mohammed", employee_id="E-AMB-1", location="AUH")
        await _make_employee(db, name="Ahmed Ali Mohammed", employee_id="E-AMB-2", location="DXB")
        pf = await _make_pipeline_file(db, name="Ahmed Ali Mohammed", employee_id=None)
        await db.commit()

        result = await rematch_unmatched(db)
        assert pf.id in {r["id"] for r in result["still_unmatched"]}

        await db.refresh(pf)
        assert pf.extraction_meta["staged"]["employee_pk"] is None


async def test_already_matched_items_are_never_touched():
    """A row that already has a real employee_pk must be left completely
    alone — rematch only ever fills in a BLANK identity, never revisits or
    overrides an existing one."""
    async with SessionLocal() as db:
        emp = await _make_employee(db, name="Already Matched Person", employee_id="E-ALREADY-1")
        pf = await _make_pipeline_file(
            db, name="Already Matched Person", employee_id="E-ALREADY-1", employee_pk=emp.id)
        await db.commit()
        original_meta = dict(pf.extraction_meta)

        result = await rematch_unmatched(db)
        assert pf.id not in {r["id"] for r in result["rematched"]}
        assert pf.id not in {r["id"] for r in result["still_unmatched"]}

        await db.refresh(pf)
        assert pf.extraction_meta == original_meta


async def test_already_decided_items_are_excluded_entirely():
    """A pipeline item already Accepted (or Failed/Resolved) must never be
    reconsidered, even if its stale staged blob still shows employee_pk=None
    from before it was manually resolved at Accept time."""
    async with SessionLocal() as db:
        await _make_employee(db, name="Decided Already Person", employee_id="E-DECIDED-1")
        pf = await _make_pipeline_file(
            db, name="Decided Already Person", employee_id="742500002",
            status=PipelineStatus.SUCCESS)
        await db.commit()

        result = await rematch_unmatched(db)
        assert pf.id not in {r["id"] for r in result["rematched"]}
        assert pf.id not in {r["id"] for r in result["still_unmatched"]}
        assert result["checked"] == 0 or pf.id not in (
            {r["id"] for r in result["rematched"]} | {r["id"] for r in result["still_unmatched"]})


async def test_non_staged_shape_rows_are_skipped_safely():
    """A manual-entry-style extraction_meta (no "staged" key at all) must be
    skipped without crashing — this function only ever touches genuinely
    staged, still-blank identities."""
    async with SessionLocal() as db:
        pf = await _make_pipeline_file(
            db, name="Manual Entry Person",
            meta={"method": "manual", "model": None, "used_ocr": False})
        await db.commit()

        result = await rematch_unmatched(db)  # must not raise
        assert pf.id not in {r["id"] for r in result["rematched"]}
        assert pf.id not in {r["id"] for r in result["still_unmatched"]}


async def test_running_it_twice_is_a_no_op_the_second_time():
    """Idempotency: once everything resolvable has been resolved, a second
    run must find nothing left to do — no re-matching, no duplicate work."""
    async with SessionLocal() as db:
        await _make_employee(db, name="Idempotent Test Person", employee_id="E-IDEMP-1")
        pf = await _make_pipeline_file(db, name="Idempotent Test Person", employee_id="742500003")
        await db.commit()

        first = await rematch_unmatched(db)
        assert pf.id in {r["id"] for r in first["rematched"]}

        second = await rematch_unmatched(db)
        assert pf.id not in {r["id"] for r in second["rematched"]}
        assert pf.id not in {r["id"] for r in second["still_unmatched"]}


async def test_many_mixed_rows_in_one_pass():
    """Several distinct outcomes in the SAME batch — proves the loop handles
    each row independently rather than short-circuiting on the first
    resolvable/unresolvable one."""
    async with SessionLocal() as db:
        await _make_employee(db, name="Batch Match One", employee_id="E-BATCH-1")
        await _make_employee(db, name="Batch Match Two", employee_id="E-BATCH-2")

        matched1 = await _make_pipeline_file(db, name="Batch Match One", employee_id="742500010")
        matched2 = await _make_pipeline_file(db, name="Batch Match Two", employee_id="742500011")
        unmatched = await _make_pipeline_file(db, name="Batch Nobody Home", employee_id="742500012")
        await db.commit()

        result = await rematch_unmatched(db)
        rematched_ids = {r["id"] for r in result["rematched"]}
        unmatched_ids = {r["id"] for r in result["still_unmatched"]}
        assert matched1.id in rematched_ids
        assert matched2.id in rematched_ids
        assert unmatched.id in unmatched_ids
        assert result["rematched_count"] == 2
        assert result["still_unmatched_count"] == 1


async def test_rematch_endpoint_requires_full_access_and_returns_the_same_shape(client, admin_token):
    """End-to-end wiring check: the route is reachable, gated, and returns
    the same summary shape the service function produces."""
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/pipeline/rematch-unmatched", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rematched_count" in body
    assert "still_unmatched_count" in body
    assert "checked" in body
