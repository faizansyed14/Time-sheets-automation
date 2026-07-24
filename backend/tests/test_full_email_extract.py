"""Extract Email — the one-button flow: full .eml in, per-employee review items out.

Runs without an API key (per-sheet engine fallback), so the whole path —
collect sheets inside the .eml → analyse → group per employee+month → stage —
is exercised deterministically.
"""
from sqlalchemy import select

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


def _sheet(name, kind="timesheet", emp_id=None, emp_name=None,
           month=6, year=2026, signature=False, **buckets):
    return {
        "name": name, "kind": kind,
        "employee_name": emp_name, "employee_id": emp_id,
        "month": month, "year": year,
        "buckets": {b: sorted(buckets.get(b, [])) for b in fx._BUCKETS},
        "manager_signature": signature, "approval_evidence": "",
    }


def _mail(**kw) -> EmailMessage:
    base = dict(provider_message_id="X", sender_name="S", sender_email="s@x.y",
                subject="t", body_text="", attachments=[])
    base.update(kw)
    return EmailMessage(**base)


# --------------------------------------------------------------------------- #
# End-to-end on the mock inbox (MSG-0001: one employee, PDF + approval png)
# --------------------------------------------------------------------------- #
async def test_single_employee_email_becomes_one_review_item():
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

        # Re-running reuses the tracker — no duplicates in the pipeline.
        res2 = await fx.extract_full_email(db, email)
        assert [x.id for x in res2["staged"]] == [t.id]


async def test_extract_full_endpoint(client, admin_token):
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
# Grouping edge cases (synthetic analyst output — no LLM, no provider)
# --------------------------------------------------------------------------- #
async def test_certificates_fold_into_the_single_identified_employee():
    """The reported case: 1 attendance sheet + 2 nameless sick certificates,
    all one person → ONE review item with the sick days unioned in."""
    async with SessionLocal() as db:
        sheets = [
            _sheet("attendance.pdf", emp_id="E2406843", emp_name="Taha Elmunzir",
                   annual=["2026-06-08"], sick=["2026-06-10"]),
            _sheet("cert1.pdf", kind="leave_certificate", sick=["2026-06-10", "2026-06-11"]),
            _sheet("cert2.pdf", kind="leave_certificate", sick=["2026-06-22"]),
        ]
        groups = await fx._group_sheets(db, _mail(), sheets)
        assert len(groups) == 1, [g["name"] for g in groups]
        g = groups[0]
        assert len(g["sheets"]) == 3
        merged, _ = fx._union_group_buckets(g["sheets"])
        assert merged["sick"] == ["2026-06-10", "2026-06-11", "2026-06-22"]
        # the fold is surfaced to the reviewer, never silent
        assert g["fold_notes"], "expected a fold note for the nameless certificates"


async def test_multiple_employees_split_and_unknowns_stay_separate():
    """A manager forwarding a batch: one item per employee; a sheet with no
    readable identity is NEVER guessed into someone's item."""
    async with SessionLocal() as db:
        sheets = [
            _sheet("a.pdf", emp_id="E1", emp_name="Alice One", annual=["2026-06-02"]),
            _sheet("b.pdf", emp_id="E2", emp_name="Bob Two", sick=["2026-06-03"]),
            _sheet("mystery.pdf"),  # no identity
        ]
        groups = await fx._group_sheets(db, _mail(), sheets)
        assert len(groups) == 3
        unassigned = [g for g in groups if not g["name"]]
        assert len(unassigned) == 1 and "manually" in unassigned[0]["note"]


async def test_same_employee_two_formats_union_with_conflict_flag():
    """ADR-format + client-format sheet for the same month → ONE item; a date
    claimed by both files is flagged, not double counted. A different month
    becomes its own item."""
    async with SessionLocal() as db:
        sheets = [
            _sheet("adr_format.pdf", emp_id="E9", emp_name="Sam Nine",
                   annual=["2026-06-02", "2026-06-03"]),
            _sheet("client_format.xlsx", emp_id="E9", emp_name="Sam Nine",
                   annual=["2026-06-03", "2026-06-04"]),
            _sheet("july.pdf", emp_id="E9", emp_name="Sam Nine",
                   month=7, annual=["2026-07-01"]),
        ]
        groups = await fx._group_sheets(db, _mail(), sheets)
        assert len(groups) == 2, [(g["month"], g["year"]) for g in groups]
        june = next(g for g in groups if g["month"] == 6)
        assert june["buckets"]["annual"] == ["2026-06-02", "2026-06-03", "2026-06-04"]
        assert any("2026-06-03" in f for f in june["overlap_flags"])
        july = next(g for g in groups if g["month"] == 7)
        assert july["buckets"]["annual"] == ["2026-07-01"]


_PASTED_GRID_BODY = """Approved.

Regards,
Sylvia Noronha

From: Kevin Dsouza <kedsouza@altayer.com>
Subject: TIMESHEET for June 2026 | Kevin Dsouza | E2507067

Hi Sylvia,

Requesting your approval for the below timesheet for the month of June.

ATTENDANCE SHEET
EMP NO : E2507067
NAME:   Kevin Dsouza
MONTH: June
YEAR: 2026

1 June 26  9:00 am  6:00 pm  8
15 June 26 Holiday Islamic New Year
16 June 26 9:00 am  6:00 pm  8

Thanks & Regards,
Kevin Dsouza"""


async def test_pasted_grid_body_reaches_the_model_and_stages():
    """A timesheet pasted as TEXT in the body (forwarded thread, manager's
    'Approved.' on top) must go to the model as its own sheet — full text in
    the prompt — and a timesheet verdict for it must stage normally."""
    from app.services.email_provider import get_email_provider
    from app.services.inbox.eml_export import build_full_eml

    async with SessionLocal() as db:
        mail = _mail(provider_message_id="BODY-GRID-1",
                     subject="RE: TIMESHEET for June 2026 | Kevin Dsouza | E2507067",
                     body_text=_PASTED_GRID_BODY)
        eml, _ = await build_full_eml(get_email_provider(), mail)

        # The body is a sheet unit carrying its full text.
        units = fx._collect_units(mail, eml)
        body_unit = next(u for u in units if u.name == "(email body)")
        assert "ATTENDANCE SHEET" in body_unit.text
        prompt = fx._extract_prompt(mail, body_unit)
        assert "(email body)" in prompt and "E2507067" in prompt

        # Simulate the model's per-sheet verdict → the rest of the flow works.
        sheet = fx._normalize_sheet(body_unit, {
            "kind": "timesheet", "employee_name": "Kevin Dsouza",
            "employee_id": "E2507067", "month": 6, "year": 2026,
            "public_holiday": ["2026-06-15"],
            "manager_signature": False,
            "approval_evidence": "Approved. — Sylvia Noronha",
        })
        assert sheet["buckets"]["public_holiday"] == ["2026-06-15"]
        # Grid-row wording ("15 June 26") normalises too.
        assert fx._clean_dates(["15 June 26"], 6, 2026) == ["2026-06-15"]

        groups = await fx._group_sheets(db, mail, [sheet])
        assert len(groups) == 1 and groups[0]["month"] == 6 and groups[0]["year"] == 2026
        approval = fx._detect_approval(mail, [sheet])
        assert approval["detected"] and "Sylvia" in approval["detail"]

        staged = await fx._stage_groups(
            db, source_kind="email", source_id=mail.provider_message_id,
            raw_bytes=eml, raw_name="kevin.eml", content_type="message/rfc822",
            groups=groups, approval=approval,
            run_meta={"method": "vision", "model": "gpt-4o", "calls": 1,
                      "sheet_count": 1, "errors": []})
        assert staged[0].employee_id == "E2507067" and staged[0].month == 6


async def test_accept_files_the_reviewers_approval_verdict():
    """Compare & Fix Accept carries the reviewer's Approved / Not approved
    verdict onto the TimesheetRecord."""
    from sqlalchemy import select as _select
    from app.models.employee import Employee
    from app.services.pipeline.ingestion import ingest_manual_entry

    async with SessionLocal() as db:
        emp = (await db.execute(_select(Employee).where(
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


def test_filename_hints_upgrade_other_timesheets():
    """When vision returns kind=other, obvious attachment names still stage."""
    unit_may = fx.SheetUnit(
        "TIMESHEET_MAY_2026_SAOODABDURAHMAN_E2206304.pdf", "pdf", b"")
    unit_jun = fx.SheetUnit(
        "TIMESHEET_JUNE_2026_SAOODABDURAHMAN_E2206304.pdf", "pdf", b"")
    unit_sick = fx.SheetUnit("Sick Leave June 5-6- Saood.pdf", "pdf", b"")
    subject = "Fw: TIMESHEET for JUNE 2026 | SAOOD ABDURAHMAN | E2206304"

    may = fx._boost_sheet_from_hints(
        fx._normalize_sheet(unit_may, {"kind": "other"}), unit_may, subject)
    jun = fx._boost_sheet_from_hints(
        fx._normalize_sheet(unit_jun, {"kind": "other"}), unit_jun, subject)
    sick = fx._boost_sheet_from_hints(
        fx._normalize_sheet(unit_sick, {"kind": "other"}), unit_sick, subject)

    assert may["kind"] == "timesheet" and may["month"] == 5 and may["year"] == 2026
    assert may["employee_id"] == "E2206304"
    assert jun["kind"] == "timesheet" and jun["month"] == 6
    assert sick["kind"] == "leave_certificate" and sick["month"] == 6


async def test_filename_hints_group_saood_email():
    async with SessionLocal() as db:
        mail = _mail(
            subject="Fw: TIMESHEET for JUNE 2026 | SAOOD ABDURAHMAN | E2206304",
            sender_email="saood.a@adnocdistribution.ae",
            body_text="Approved.\nRegards,\nWafa",
        )
        sheets = [
            fx._boost_sheet_from_hints(
                fx._normalize_sheet(
                    fx.SheetUnit("TIMESHEET_MAY_2026_SAOODABDURAHMAN_E2206304.pdf", "pdf", b""),
                    {"kind": "other"}),
                fx.SheetUnit("TIMESHEET_MAY_2026_SAOODABDURAHMAN_E2206304.pdf", "pdf", b""),
                mail.subject),
            fx._boost_sheet_from_hints(
                fx._normalize_sheet(
                    fx.SheetUnit("TIMESHEET_JUNE_2026_SAOODABDURAHMAN_E2206304.pdf", "pdf", b""),
                    {"kind": "other"}),
                fx.SheetUnit("TIMESHEET_JUNE_2026_SAOODABDURAHMAN_E2206304.pdf", "pdf", b""),
                mail.subject),
            fx._boost_sheet_from_hints(
                fx._normalize_sheet(
                    fx.SheetUnit("Sick Leave June 5-6- Saood.pdf", "pdf", b""),
                    {"kind": "other"}),
                fx.SheetUnit("Sick Leave June 5-6- Saood.pdf", "pdf", b""),
                mail.subject),
        ]
        groups = await fx._group_sheets(db, mail, sheets)
        assert len(groups) == 2, [(g["month"], g["year"]) for g in groups]
        assert fx._detect_approval(mail, sheets)["detected"]


def test_approval_detection_signature_screenshot_and_body():
    """Approval is read by the MODEL: signature on a sheet, an approval
    screenshot, or approval wording in the body (which is itself an analysed
    sheet). The pattern check only exists for the keyless fallback."""
    ts = _sheet("t.pdf", signature=True)
    appr = _sheet("shot.png", kind="approval")
    appr["approval_evidence"] = "Approved by Sarah"
    body = _sheet("(email body)", kind="other")
    body["approval_evidence"] = "Approved, please process"
    assert fx._detect_approval(_mail(), [ts])["detected"]
    assert fx._detect_approval(_mail(), [appr])["detected"]
    assert fx._detect_approval(_mail(), [body])["detected"]
    # Body pattern backstop when the model missed approval wording.
    assert fx._detect_approval(
        _mail(body_text="Approved, please process."), [], used_vision=True)["detected"]
    assert not fx._detect_approval(
        _mail(body_text="This is not approved yet."), [_sheet("t.pdf")],
        used_vision=True)["detected"]
