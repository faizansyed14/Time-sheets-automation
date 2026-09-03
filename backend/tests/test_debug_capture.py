"""debug_capture — full raw-prompt/response + dropped-image trace for one
extraction run (see /admin/debug). A temporary testing aid: this locks in
that the capture actually collects what it claims to (pass-1/pass-2 calls,
dropped items with a saved full-resolution image) and that nothing is
captured — a true no-op — when no capture was started, which is the
default, non-debug path every other test in this suite already exercises.
"""
from email.message import EmailMessage as MimeMessage

from app.services.extract_email import debug_capture
from app.services.extract_email.thread_extract import extract_thread_sheets


def _mail(*, subject="TIMESHEET June 2026", plain="", attachments=()):
    m = MimeMessage()
    m["Subject"] = subject
    m["From"] = "employee@alpha.ae"
    m["To"] = "timesheet@alpha.ae"
    m.set_content(plain or "See attached.")
    for fn, payload, maintype, subtype in attachments:
        m.add_attachment(payload, maintype=maintype, subtype=subtype, filename=fn)
    return m.as_bytes()


def _png_bytes(*, big: bool) -> bytes:
    import io
    import random

    from PIL import Image

    side = 400 if big else 20
    rng = random.Random(0)
    img = Image.new("RGB", (side, side))
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                 for _ in range(side * side)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pass1_response():
    return {"thread_summary": "", "items": [{
        "source": "[A2]", "is_timesheet": True, "kind": "timesheet",
        "employee_name": "Test Person", "employee_id": "E1",
        "period_hint": "June 2026", "evidence": "1-June-26 present",
        "manager_signature": False, "signature_evidence": "", "notes": "",
    }]}


def _pass2_response():
    return {"sheets": [{
        "source": "[A2]", "employee_name": "Test Person", "employee_id": "E1",
        "month": 6, "year": 2026, "days_covered": 1, "period_type": "partial",
        "missing_days": [], "working_days": ["2026-06-01"], "weekend_days": [],
        "uncertain_days": [], "annual": [], "remote": [], "sick": [],
        "maternity": [], "unpaid": [], "absent": [], "public_holiday": [], "notes": "",
    }]}


async def test_capture_records_pass1_and_pass2_calls_and_a_dropped_image(mock_vision_calls):
    tiny_png = _png_bytes(big=False)   # well under MIN_IMAGE_BYTES (60KB) -> size-dropped
    eml = _mail(plain="See attached.", attachments=[
        ("logo.png", tiny_png, "image", "png"),
        ("sheet.pdf", b"%PDF-1.4 fake sheet", "application", "pdf"),
    ])
    mock_vision_calls([_pass1_response(), _pass2_response()])

    cap = debug_capture.DebugCapture()
    token = debug_capture.set_capture(cap)
    try:
        sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])
    finally:
        debug_capture.reset_capture(token)

    assert len(sheets) == 1, "sanity check — extraction itself must still succeed while capturing"

    assert len(cap.pass1_calls) == 1
    assert cap.pass1_calls[0]["response_json"]["items"][0]["employee_name"] == "Test Person"
    # Real captured prompt text, not a placeholder — a distinctive phrase
    # from the actual PASS1_SYSTEM constant.
    assert "UAE HR analyst" in cap.pass1_calls[0]["system_prompt"]

    assert len(cap.pass2_calls) == 1
    assert cap.pass2_calls[0]["response_json"]["sheets"][0]["month"] == 6

    assert len(cap.dropped_items) == 1
    dropped = cap.dropped_items[0]
    assert dropped["filter"] == "size"
    assert dropped["name"] == "logo.png"
    assert dropped["image_path"], "a small dropped image must get a saved full-resolution image_path"
    assert dropped["image_path"].startswith(f"debug/{cap.run_id}/")

    # And it's actually readable back from where it claims to be — saved as
    # the rendered JPEG (same as the thumbnail is built from), not the
    # original PNG bytes.
    from app.services.pipeline import raw_store
    saved = raw_store.read_raw(dropped["image_path"])
    assert saved and saved.startswith(b"\xff\xd8"), "expected a real JPEG at the saved path"
    raw_store.delete_raw(dropped["image_path"])


async def test_no_capture_active_is_a_pure_no_op(mock_vision_calls):
    """Nothing is captured unless a capture was explicitly started — the
    default (non-debug) path every other test in this suite runs through
    must be completely unaffected by this module existing."""
    assert debug_capture.get_capture() is None
    tiny_png = _png_bytes(big=False)
    eml = _mail(plain="See attached.", attachments=[("logo.png", tiny_png, "image", "png")])
    mock_vision_calls([{"thread_summary": "", "items": []}])

    sheets, approval, conflicts, meta = await extract_thread_sheets([("msg 1", eml)])

    assert debug_capture.get_capture() is None
    # record_* are silent no-ops with no active capture — nothing raised above.


async def _get_or_sync_msg0001(db):
    from sqlalchemy import select

    from app.models.email_message import EmailMessage

    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == "MSG-0001"))).scalar_one_or_none()
    if row is None:
        from app.services.email_provider import get_email_provider
        from app.services.inbox.sync import sync_message
        msg = await get_email_provider().get_message("MSG-0001")
        row = await sync_message(db, msg)
        await db.commit()
        await db.refresh(row)
    return row


async def test_extract_full_email_clean_success_does_not_persist_a_debug_run(mock_vision_calls):
    """A run that went cleanly (extracted at least one sheet, no errors)
    must leave NOTHING in the debug table — this is a failure log now, not
    a trace of every run. The in-memory capture (tested above) still
    happens either way; only the DB write is conditional."""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.extraction_debug_run import ExtractionDebugRun
    from app.services.agents import full_email_extract as fx

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
        row = await _get_or_sync_msg0001(db)
        before = (await db.execute(select(ExtractionDebugRun.id))).scalars().all()
        res = await fx.extract_full_email(db, row)
        assert res["groups"] == 1, res["message"]

        after = (await db.execute(select(ExtractionDebugRun.id))).scalars().all()
        assert len(after) == len(before), "a clean, successful run must not create a debug row"


async def test_extract_full_email_persists_a_debug_run_when_pass1_errors(mock_vision_calls):
    """The other half of the same rule: a run that hit a real problem (here,
    a pass-1 batch raising — see mock_vision_calls' own docstring for how to
    script that) DOES get a persisted, browsable ExtractionDebugRun row."""
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.extraction_debug_run import ExtractionDebugRun
    from app.services.agents import full_email_extract as fx

    mock_vision_calls([RuntimeError("simulated pass-1 failure")])

    async with SessionLocal() as db:
        row = await _get_or_sync_msg0001(db)
        before = (await db.execute(select(ExtractionDebugRun.id))).scalars().all()
        await fx.extract_full_email(db, row)

        after = (await db.execute(select(ExtractionDebugRun)
                                  .order_by(ExtractionDebugRun.created_at.desc()))).scalars().all()
        assert len(after) == len(before) + 1, "a run with a pass-1 error must create exactly one debug row"
        run = after[0]
        assert run.source_kind == "email"
        assert run.errors and "simulated pass-1 failure" in run.errors[0]

        await db.delete(run)
        await db.commit()


async def test_capture_is_scoped_to_its_own_run():
    """A capture only ever sees calls made while IT is the active context —
    starting a second capture must not leak into the first's lists."""
    cap1 = debug_capture.DebugCapture()
    token1 = debug_capture.set_capture(cap1)
    debug_capture.record_pass1(label="from-cap1")
    debug_capture.reset_capture(token1)

    cap2 = debug_capture.DebugCapture()
    token2 = debug_capture.set_capture(cap2)
    debug_capture.record_pass1(label="from-cap2")
    debug_capture.reset_capture(token2)

    assert [c["label"] for c in cap1.pass1_calls] == ["from-cap1"]
    assert [c["label"] for c in cap2.pass1_calls] == ["from-cap2"]


# --------------------------------------------------------------------------- #
# raw_store.read_raw — path-traversal containment
#
# admin.py's GET /admin/debug/image takes rel_path straight off a query
# param, gated only by `rel_path.startswith("debug/")` — a STRING prefix
# check, not a path guard. "debug/../../../<secret>" satisfies it. The real
# guard has to live in read_raw itself (or nothing stops it reaching disk).
# --------------------------------------------------------------------------- #
async def test_read_raw_rejects_path_traversal_outside_the_raw_root():
    from app.core.config import settings
    from app.services.pipeline import raw_store

    outside_dir = settings.pipeline_raw_path.parent
    secret = outside_dir / "secret-outside-raw-root.txt"
    secret.write_bytes(b"must never be readable through raw_store")
    try:
        traversal = "debug/" + "../" * 6 + "secret-outside-raw-root.txt"
        assert raw_store.read_raw(traversal) is None
    finally:
        secret.unlink(missing_ok=True)


async def test_read_raw_still_reads_a_legitimately_saved_file():
    """The containment fix must not break the normal, non-malicious path."""
    from app.services.pipeline import raw_store

    rel = raw_store.save_raw("debug-containment-test-id", "note.txt", b"hello")
    assert rel
    try:
        assert raw_store.read_raw(rel) == b"hello"
    finally:
        raw_store.delete_raw(rel)


async def test_debug_image_route_rejects_traversal_even_though_it_starts_with_debug(client, admin_token):
    from tests.conftest import auth_headers

    h = auth_headers(admin_token)
    r = await client.get("/api/v1/admin/debug/image", headers=h, params={
        "rel_path": "debug/" + "../" * 6 + "secret-outside-raw-root.txt",
    })
    assert r.status_code == 400, r.text


async def test_read_portal_file_rejects_path_traversal_outside_its_own_root():
    """Same containment gap, same fix, in the employee-portal upload store
    (every real caller passes rel_path from a DB row, never a request param
    directly — but the fix costs nothing, so it applies here too)."""
    from app.core.config import settings
    from app.services.pipeline import portal_store

    outside_dir = settings.portal_uploads_path.parent
    secret = outside_dir / "secret-outside-portal-root.txt"
    secret.write_bytes(b"must never be readable through portal_store")
    try:
        traversal = "sub/" + "../" * 6 + "secret-outside-portal-root.txt"
        assert portal_store.read_portal_file(traversal) is None
    finally:
        secret.unlink(missing_ok=True)
