"""One email, several employees: each record files ITS OWN sheets plus the approval
evidence that applies to it (a manager's screenshot), never another person's sheet."""
from email.message import EmailMessage as Mime

from app.models.pipeline_file import PipelineFile
from app.services.extract_email.thread_extract import Item, _approval_files
from app.services.pipeline.ingestion import isolate_employee_attachments


def _email() -> bytes:
    m = Mime()
    m["Subject"] = "October timesheets"
    m.set_content("Please find the timesheets, approval attached.")
    for name, data, sub in (("ali_timesheet.pdf", b"%PDF-1.4 ali", "pdf"),
                            ("ali_sick_certificate.pdf", b"%PDF-1.4 sick", "pdf"),
                            ("sara_timesheet.pdf", b"%PDF-1.4 sara", "pdf"),
                            ("manager_approval.png", b"\x89PNG approval", "png"),
                            ("approval_sara_only.png", b"\x89PNG sara-approval", "png")):
        main = "image" if sub == "png" else "application"
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m.as_bytes()


def _item(name: str, employee: str) -> PipelineFile:
    return PipelineFile(source_kind="email", employee_name=employee, extraction_meta={"full_email_extract": {
        "group_count": 2,
        "sheets": [{"filename": f} for f in ({"Ali Khan": ["ali_timesheet.pdf", "ali_sick_certificate.pdf"],
                                              "Sara Noor": ["sara_timesheet.pdf"]}[employee])],
        "approval_files": [{"name": "manager_approval.png", "employee_name": ""},
                           {"name": "approval_sara_only.png", "employee_name": "Sara Noor"}],
    }})


def test_each_record_files_its_sheets_and_the_approval_that_applies():
    raw = _email()
    ali = sorted(n for n, _c, _d in isolate_employee_attachments(_item("x", "Ali Khan"), raw))
    sara = sorted(n for n, _c, _d in isolate_employee_attachments(_item("x", "Sara Noor"), raw))
    assert ali == ["ali_sick_certificate.pdf", "ali_timesheet.pdf", "manager_approval.png"]
    assert sara == ["approval_sara_only.png", "manager_approval.png", "sara_timesheet.pdf"]


def test_a_missing_approval_file_never_blocks_filing_the_sheets():
    m = Mime()
    m["Subject"] = "x"
    m.set_content("x")
    m.add_attachment(b"%PDF-1.4 ali", maintype="application", subtype="pdf", filename="ali_timesheet.pdf")
    t = PipelineFile(source_kind="email", employee_name="Ali Khan", extraction_meta={"full_email_extract": {
        "group_count": 2, "sheets": [{"filename": "ali_timesheet.pdf"}],
        "approval_files": [{"name": "gone.png", "employee_name": ""}]}})
    assert [n for n, _c, _d in isolate_employee_attachments(t, m.as_bytes())] == ["ali_timesheet.pdf"]


def test_approval_files_are_taken_from_attached_files_not_email_text():
    items = [Item(key="A1", name="email body (message 1)", mime="text/plain", msg_index=0),
             Item(key="A2", name="approval.png", mime="image/png", msg_index=0)]
    triage = [{"kind": "approval", "source": "[A1]"}, {"kind": "approval", "source": "[A2]", "employee_name": "Ali"},
              {"kind": "timesheet", "source": "[A2]"}]
    assert _approval_files(triage, items) == [{"name": "approval.png", "employee_name": "Ali"}]


async def test_review_opens_the_exact_sheet_even_inside_a_forwarded_email(client, admin_token):
    import uuid
    from sqlalchemy import delete
    from app.core.database import SessionLocal
    from app.models.pipeline_file import PipelineStatus
    from app.services.extract_email.thread_collect import build_thread_bundle
    from app.services.pipeline import raw_store
    from tests.conftest import auth_headers

    import email as _email
    from email import policy as _policy
    deep = b"%PDF-1.4 " + b"0" * 4000
    inner = Mime()
    inner["Subject"] = "FW: timesheet"
    inner.set_content("forwarded")
    inner.add_attachment(deep, maintype="application", subtype="pdf", filename="deep_sheet.pdf")
    outer = Mime()
    outer["Subject"] = "Fwd"
    outer.set_content("see attached")
    outer.add_attachment(_email.message_from_bytes(inner.as_bytes(), policy=_policy.default))
    reply = Mime()
    reply["Subject"] = "RE: Fwd"
    reply.set_content("Approved.")
    bundle, name = build_thread_bundle([("m1", outer.as_bytes()), ("m2", reply.as_bytes())], "Fwd")

    pid = str(uuid.uuid4())
    rel = raw_store.save_raw(pid, name, bundle)
    async with SessionLocal() as db:
        db.add(PipelineFile(id=pid, filename=name, source_kind="email", status=PipelineStatus.NEEDS_REVIEW,
                            raw_path=rel))
        await db.commit()
    try:
        h = auth_headers(admin_token)
        r = await client.get(f"/api/v1/pipeline/{pid}/sheet", headers=h, params={"name": "deep_sheet.pdf"})
        assert r.status_code == 200, r.text
        assert r.content == deep
        assert r.headers["content-type"].startswith("application/pdf")
        miss = await client.get(f"/api/v1/pipeline/{pid}/sheet", headers=h, params={"name": "nope.pdf"})
        assert miss.status_code == 404
    finally:
        raw_store.delete_raw(rel)
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.id == pid))
            await db.commit()


def _same_name_email(contents: list[bytes]) -> bytes:
    m = Mime()
    m["Subject"] = "timesheets"
    m.set_content("attached")
    for c in contents:
        m.add_attachment(c, maintype="application", subtype="pdf", filename="Timesheet.pdf")
    return m.as_bytes()


def _rec(employee: str, digests: list[str], group_count: int = 3) -> PipelineFile:
    return PipelineFile(source_kind="email", employee_name=employee, extraction_meta={"full_email_extract": {
        "group_count": group_count,
        "sheets": [{"filename": "Timesheet.pdf", "digest": d} for d in digests]}})


def test_twenty_people_all_named_timesheet_pdf_each_get_only_their_own():
    from app.services.extract_email.thread_extract import _content_digest
    files = [b"%PDF-1.4 person-" + str(i).encode() for i in range(20)]
    raw = _same_name_email(files)
    for i in (0, 7, 19):
        got = isolate_employee_attachments(_rec(f"Person {i}", [_content_digest(files[i])], 20), raw)
        assert got is not None and [d for _n, _c, d in got] == [files[i]]


def test_one_person_with_four_weekly_sheets_of_the_same_name_keeps_all_four():
    from app.services.extract_email.thread_extract import _content_digest
    weeks = [b"%PDF-1.4 week-" + str(i).encode() for i in range(4)]
    other = b"%PDF-1.4 someone-else"
    raw = _same_name_email(weeks + [other])
    got = isolate_employee_attachments(_rec("Ali", [_content_digest(w) for w in weeks]), raw)
    assert sorted(d for _n, _c, d in got) == sorted(weeks)


def test_an_older_record_without_fingerprints_files_the_whole_email_rather_than_guess():
    raw = _same_name_email([b"%PDF-1.4 a", b"%PDF-1.4 b"])
    assert isolate_employee_attachments(_rec("Ali", [""]), raw) is None


async def test_an_uploaded_pdf_is_its_own_sheet(client, admin_token):
    import uuid
    from sqlalchemy import delete
    from app.core.database import SessionLocal
    from app.models.pipeline_file import PipelineStatus
    from app.services.pipeline import raw_store
    from tests.conftest import auth_headers

    pid = str(uuid.uuid4())
    pdf = b"%PDF-1.4 uploaded " + b"0" * 2000
    rel = raw_store.save_raw(pid, "Upload.pdf", pdf)
    async with SessionLocal() as db:
        db.add(PipelineFile(id=pid, filename="Ali Khan \u00b7 Upload.pdf", source_kind="upload",
                            status=PipelineStatus.NEEDS_REVIEW, raw_path=rel))
        await db.commit()
    try:
        r = await client.get(f"/api/v1/pipeline/{pid}/sheet", headers=auth_headers(admin_token),
                             params={"name": "Upload.pdf"})
        assert r.status_code == 200 and r.content == pdf
    finally:
        raw_store.delete_raw(rel)
        async with SessionLocal() as db:
            await db.execute(delete(PipelineFile).where(PipelineFile.id == pid))
            await db.commit()
