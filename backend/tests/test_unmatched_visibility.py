"""Sheets that match no employee are never invisible: counted on the Activity page and
the Dashboard, and listable on their own."""
from sqlalchemy import delete

from app.core import datacache
from app.core.database import SessionLocal
from app.models.pipeline_file import PipelineFile, PipelineStatus
from tests.conftest import auth_headers

YEAR, MONTH = 2033, 4


async def test_unmatched_items_are_counted_and_listed(client, admin_token):
    h = auth_headers(admin_token)
    async with SessionLocal() as db:
        for i, pk in enumerate([None, None, "some-employee-pk"]):
            db.add(PipelineFile(
                filename=f"u{i}.pdf", source_kind="email", source_id="UNM", thread_key="UNM",
                attachment_id=f"unm:{i}", status=PipelineStatus.NEEDS_REVIEW, month=MONTH, year=YEAR,
                employee_name=f"Person {i}",
                extraction_meta={"staged": {"employee_pk": pk, "month": MONTH, "year": YEAR}}))
        await db.commit()
    await datacache.bust_pipeline()
    await datacache.bust_employees()
    try:
        lst = (await client.get("/api/v1/pipeline", headers=h,
                                params={"unmatched": "true", "month": MONTH, "year": YEAR})).json()
        assert sorted(i["filename"] for i in lst["items"]) == ["u0.pdf", "u1.pdf"]
        stats = (await client.get("/api/v1/pipeline/stats", headers=h)).json()
        assert stats["unmatched"] >= 2
        cov = (await client.get("/api/v1/employees/coverage", headers=h,
                                params={"year": YEAR, "month": MONTH, "limit": 1})).json()
        assert cov["unmatched_this_month"] == 2
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.thread_key == "UNM"))
            await db.commit()
        await datacache.bust_pipeline()
        await datacache.bust_employees()


async def test_ai_recommended_count_and_list_agree(client, admin_token):
    h = auth_headers(admin_token)
    async with SessionLocal() as db:
        for i, ok in enumerate([True, True, False]):
            db.add(PipelineFile(
                filename=f"ai{i}.pdf", source_kind="email", source_id="AIR", thread_key="AIR",
                attachment_id=f"air:{i}", status=PipelineStatus.NEEDS_REVIEW, month=MONTH, year=YEAR,
                extraction_meta={"staged": {"employee_pk": "pk", "month": MONTH, "year": YEAR},
                                 "auto_accept": {"accepted": ok}}))
        await db.commit()
    await datacache.bust_pipeline()
    try:
        stats = (await client.get("/api/v1/pipeline/stats", headers=h)).json()
        lst = (await client.get("/api/v1/pipeline", headers=h, params={
            "status": "needs_review", "auto_accepted": "true", "thread_key": "AIR"})).json()
        assert sorted(i["filename"] for i in lst["items"]) == ["ai0.pdf", "ai1.pdf"]
        assert stats["ai_recommended"] >= 2
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.thread_key == "AIR"))
            await db.commit()
        await datacache.bust_pipeline()
