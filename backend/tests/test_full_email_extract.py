"""Extract Email — the one-button flow: full .eml in, per-employee review items out.

The vision model is mocked (see conftest.mock_vision_calls) — the real
collector, grouping and staging run for real; only the network call is
scripted.
"""
from sqlalchemy import select

from tests._sheet_helpers import make_sheet

from app.core.database import SessionLocal
from app.models.email_message import EmailMessage
from app.models.pipeline_file import FailureCode, PipelineStatus
from app.services.agents import full_email_extract as fx
from tests.conftest import auth_headers


async def _email(db, msg_id: str = "MSG-0001") -> EmailMessage:
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == msg_id))).scalar_one_or_none()
    if row is None:
        from app.api.routes.inbox import _sync_message
        from app.services.email_provider import get_email_provider
        msg = await get_email_provider().get_message(msg_id)
        row = await _sync_message(db, msg)
        await db.commit()
        await db.refresh(row)
    return row


def _mail(**kw) -> EmailMessage:
    base = dict(provider_message_id="X", sender_name="S", sender_email="s@x.y",
                subject="t", body_text="", attachments=[])
    base.update(kw)
    return EmailMessage(**base)


# --------------------------------------------------------------------------- #
# End-to-end on the mock inbox (MSG-0001: one employee, PDF + approval png)
# --------------------------------------------------------------------------- #
async def test_single_employee_email_becomes_one_review_item(mock_vision_calls):
    mock_vision_calls([
        {
            "thread_summary": "Mohammed Ali submitted his January timesheet.",
            "items": [{
                "source": "ts.pdf", "is_timesheet": True, "kind": "timesheet",
                "employee_name": "Mohammed Ali", "employee_id": "EMP-1001",
                "period_hint": "January 2026", "evidence": "2026-01-06 annual leave",
                "manager_signature": True, "signature_evidence": "Sarah Khan", "notes": "",
            }],
        },
        {"sheets": [{
            "source": "ts.pdf", "employee_name": "Mohammed Ali", "employee_id": "EMP-1001",
            "month": 1, "year": 2026, "days_covered": 3, "period_type": "partial",
            "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
            "annual": ["2026-01-06", "2026-01-07", "2026-01-08"], "remote": [],
            "sick": ["2026-01-20"], "maternity": [], "unpaid": [], "absent": [],
            "public_holiday": ["2026-01-01"], "notes": "",
        }]},
    ])
    async with SessionLocal() as db:
        email = await _email(db)
        res = await fx.extract_full_email(db, email)

        assert res["groups"] == 1, res["message"]
        assert len(res["staged"]) == 1
        t = res["staged"][0]
        assert t.status == PipelineStatus.NEEDS_REVIEW
        assert t.failure_code == FailureCode.PENDING_REVIEW
        # The raw copy IS the full .eml → Compare & Fix shows the whole email.
        assert t.content_type == "message/rfc822"
        assert (t.attachment_id or "").startswith(fx._TAG_PREFIX)
        from app.services.pipeline.ingestion import read_raw_copy
        assert read_raw_copy(t), "full .eml raw copy missing"

        staged = (t.extraction_meta or {})["staged"]
        assert staged["month"] == 1 and staged["year"] == 2026
        assert staged["buckets"]["annual"], "expected extracted annual-leave dates"
        # Per-sheet provenance travels with the item for the review panel.
        fe = (t.extraction_meta or {})["full_email_extract"]
        assert fe["sheets"], "sheet breakdown missing"
        assert "approval" in fe


async def test_extract_full_endpoint(client, admin_token, mock_vision_calls):
    mock_vision_calls([
        {"thread_summary": "x", "items": [{
            "source": "ts.pdf", "is_timesheet": True, "kind": "timesheet",
            "employee_name": "Mohammed Ali", "employee_id": "EMP-1001",
            "period_hint": "January 2026", "evidence": "2026-01-06 annual leave",
            "manager_signature": True, "signature_evidence": "Sarah Khan", "notes": "",
        }]},
        {"sheets": [{
            "source": "ts.pdf", "employee_name": "Mohammed Ali", "employee_id": "EMP-1001",
            "month": 1, "year": 2026, "days_covered": 3, "period_type": "partial",
            "missing_days": [], "working_days": [], "weekend_days": [], "uncertain_days": [],
            "annual": ["2026-01-06", "2026-01-07", "2026-01-08"], "remote": [],
            "sick": [], "maternity": [], "unpaid": [], "absent": [],
            "public_holiday": [], "notes": "",
        }]},
    ])
    async with SessionLocal() as db:
        await _email(db)
    h = auth_headers(admin_token)
    r = await client.post("/api/v1/inbox/MSG-0001/extract-full", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["groups"] == 1
    assert len(body["staged"]) == 1
    assert body["staged"][0]["failure_code"] == "pending_review"
    assert body["message"]

    detail = await client.get("/api/v1/inbox/MSG-0001", headers=h)
    assert detail.status_code == 200
    assert detail.json()["extract_email_at"] is not None

    listing = await client.get("/api/v1/inbox", headers=h)
    msg = next(i for i in listing.json()["items"] if i["provider_message_id"] == "MSG-0001")
    assert msg["extract_email_at"] is not None


# --------------------------------------------------------------------------- #
# Grouping edge cases (synthetic pass-2 output — no LLM, no provider)
# --------------------------------------------------------------------------- #
async def test_certificates_fold_into_the_single_identified_employee():
    """The reported case: 1 attendance sheet + 2 nameless sick certificates,
    all one person → ONE review item with the sick days unioned in."""
    async with SessionLocal() as db:
        sheets = [
            make_sheet("attendance.pdf", employee_id="E2406843", employee_name="Taha Elmunzir",
                      period_type="partial", days_covered=1, annual=["2026-06-08"], sick=["2026-06-10"]),
            make_sheet("cert1.pdf", kind="leave_certificate", sick=["2026-06-10", "2026-06-11"]),
            make_sheet("cert2.pdf", kind="leave_certificate", sick=["2026-06-22"]),
        ]
        groups = await fx._group_sheets(db, _mail(), sheets)
        assert len(groups) == 1, [g["name"] for g in groups]
        g = groups[0]
        assert len(g["sheets"]) == 3
        assert g["buckets"]["sick"] == ["2026-06-10", "2026-06-11", "2026-06-22"]
        # the fold is surfaced to the reviewer, never silent
        assert g["fold_notes"], "expected a fold note for the nameless certificates"


async def test_multiple_employees_split_and_unknowns_stay_separate():
    """A manager forwarding a batch: one item per employee; a sheet with no
    readable identity is NEVER guessed into someone's item."""
    async with SessionLocal() as db:
        sheets = [
            make_sheet("a.pdf", employee_id="E1", employee_name="Alice One", annual=["2026-06-02"]),
            make_sheet("b.pdf", employee_id="E2", employee_name="Bob Two", sick=["2026-06-03"]),
            make_sheet("mystery.pdf"),  # no identity
        ]
        groups = await fx._group_sheets(db, _mail(), sheets)
        assert len(groups) == 3
        unassigned = [g for g in groups if not g["name"]]
        assert len(unassigned) == 1 and "manually" in unassigned[0]["note"]


async def test_same_employee_two_sheets_union_with_conflict_flag():
    """Two sheets for the same employee+month → ONE item; a date claimed by
    both is flagged, not double counted. A different month becomes its own
    item."""
    async with SessionLocal() as db:
        sheets = [
            make_sheet("adr_format.pdf", employee_id="E9", employee_name="Sam Nine",
                      annual=["2026-06-02", "2026-06-03"]),
            make_sheet("client_format.xlsx", employee_id="E9", employee_name="Sam Nine",
                      annual=["2026-06-03", "2026-06-04"]),
            make_sheet("july.pdf", employee_id="E9", employee_name="Sam Nine",
                      month=7, annual=["2026-07-01"]),
        ]
        groups = await fx._group_sheets(db, _mail(), sheets)
        assert len(groups) == 2, [(g["month"], g["year"]) for g in groups]
        june = next(g for g in groups if g["month"] == 6)
        assert june["buckets"]["annual"] == ["2026-06-02", "2026-06-03", "2026-06-04"]
        july = next(g for g in groups if g["month"] == 7)
        assert july["buckets"]["annual"] == ["2026-07-01"]


async def test_pasted_grid_body_reaches_the_model_and_stages():
    """A timesheet pasted as TEXT in the body must go to the model as its own
    item — full text — and a timesheet verdict for it must stage normally."""
    async with SessionLocal() as db:
        mail = _mail(provider_message_id="BODY-GRID-1",
                     subject="RE: TIMESHEET for June 2026 | Kevin Dsouza | E2507067")

        sheet = make_sheet("email body (message 1)", employee_name="Kevin Dsouza",
                           employee_id="E2507067", month=6, year=2026,
                           period_type="partial", days_covered=2,
                           public_holiday=["2026-06-15"],
                           manager_signature=False,
                           approval_evidence="Approved. — Sylvia Noronha")
        assert sheet["public_holiday"] == ["2026-06-15"]

        groups = await fx._group_sheets(db, mail, [sheet])
        assert len(groups) == 1 and groups[0]["month"] == 6 and groups[0]["year"] == 2026

        staged = await fx._stage_groups(
            db, source_kind="email", source_id=mail.provider_message_id,
            raw_bytes=b"eml-bytes", raw_name="kevin.eml", content_type="message/rfc822",
            groups=groups, approval={"detected": True, "detail": "Approved by Sylvia Noronha"},
            run_meta={"method": "thread-two-pass", "model": "test-model", "calls": 2,
                      "sheet_count": 1, "errors": []})
        assert staged[0].employee_id == "E2507067" and staged[0].month == 6


async def test_accept_files_the_reviewers_approval_verdict():
    """Compare & Fix Accept carries the reviewer's Approved / Not approved
    verdict onto the TimesheetRecord."""
    from app.models.employee import Employee
    from app.services.pipeline.ingestion import ingest_manual_entry

    async with SessionLocal() as db:
        emp = (await db.execute(select(Employee).where(
            Employee.employee_id == "E-APPR-1"))).scalars().first()
        if emp is None:
            emp = Employee(employee_id="E-APPR-1", name="Appr Test",
                           account_manager="AM One", location="DXB")
            db.add(emp)
            await db.commit()
            await db.refresh(emp)

        rec, _t = await ingest_manual_entry(
            db, employee_pk=emp.id, month=6, year=2026,
            buckets={"public_holiday": ["2026-06-15"]},
            approval={"approved": True, "detail": "Approved — Sylvia, 2 Jul 2026"})
        assert rec.approval_status == "approved"
        assert rec.approval_detected is True
        assert "Sylvia" in (rec.approval_detail or "")

        rec2, _t2 = await ingest_manual_entry(
            db, employee_pk=emp.id, month=6, year=2026, buckets={},
            approval={"approved": False, "detail": ""})
        assert rec2.approval_status == "not_approved"
        assert rec2.approval_detected is False
