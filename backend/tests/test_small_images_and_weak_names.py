"""Two settings the owner chose, plus the low-confidence name fallback.

1. Pictures under 30 KB that are embedded in the body or carry a generic mail-client name
   (image001.png) are logos/icons: dropped, recorded. A picture attached under a real file
   name is always read, however small. Under 1 KB is always a tracking pixel.
2. Pass 1 (classify) runs at low image detail, pass 2 (read the digits) at high; the second
   look and the copy comparison always run at high.
3. When the NAME only partly matches an employee, the printed ID or the sender's own address
   can back it up - as a low-confidence, always-flagged suggestion that is never recommended
   for acceptance. Evidence that disagrees, or a lone shared first name, guesses nothing.
"""
import io
from email.message import EmailMessage as Mime
from types import SimpleNamespace

from PIL import Image

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.employee import Employee
from app.services.extract_email.grouping import group_sheets
from app.services.extract_email.thread_extract import _is_generic_image_name, collect_thread
from app.services.pipeline import matching
from app.services.pipeline.matching import MatchCode, _partial_name_overlap


def _png(target_bytes: int) -> bytes:
    """A real PNG of roughly `target_bytes` (noise so it does not compress away)."""
    import os
    side = 8
    while True:
        im = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        if len(buf.getvalue()) >= target_bytes or side > 600:
            return buf.getvalue()
        side = int(side * 1.4) + 1


def _thread(*atts):
    m = Mime()
    m["Subject"] = "sheets"
    m.set_content("hi")
    for name, data, inline in atts:
        m.add_attachment(data, maintype="image", subtype="png", filename=name,
                         disposition="inline" if inline else "attachment",
                         cid=f"<{name}@x>" if inline else None)
    return collect_thread([("m1", m.as_bytes())])


def test_default_threshold_is_30kb():
    assert settings.min_image_bytes == 30 * 1024


def test_generic_names():
    for n in ("image001.png", "image.png", "Outlook-abc123.png", "c2_signature_1.png", "", None, "noname"):
        assert _is_generic_image_name(n), n
    for n in ("5thSept-11sept.png", "timesheet.png", "week 2.png", "IMG_2041.jpg"):
        assert not _is_generic_image_name(n), n


def test_small_logos_are_dropped_and_recorded_but_a_named_attachment_is_kept():
    small = _png(5_000)
    assert 1024 < len(small) < settings.min_image_bytes
    th = _thread(("image001.png", small, False),          # generic name  -> logo
                 ("logo-cid.png", small, True),            # embedded      -> logo
                 ("5thSept-11sept.png", small, False))     # real name     -> always read
    kept = [i.name for i in th.items if not i.name.startswith("email body")]
    assert kept == ["5thSept-11sept.png"], kept
    dropped = {d["name"]: d for d in th.dropped}
    assert set(dropped) == {"image001.png", "logo-cid.png"}
    assert all(d["filter"] == "size" and "30KB" in d["reason"] for d in dropped.values()), dropped


def test_a_large_embedded_or_generic_picture_is_not_dropped_by_size():
    big = _png(settings.min_image_bytes + 5_000)
    th = _thread(("image002.png", big, False))
    assert [i.name for i in th.items if not i.name.startswith("email body")] == ["image002.png"]
    assert not [d for d in th.dropped if d["filter"] == "size"]


def test_a_tracking_pixel_is_always_dropped_even_with_a_real_name():
    th = _thread(("timesheet.png", _png(0)[:0] or _tiny(), False))
    assert not [i for i in th.items if not i.name.startswith("email body")]
    assert th.dropped and "tracking pixel" in th.dropped[0]["reason"]


def _tiny() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buf, "PNG")
    assert len(buf.getvalue()) < 1024
    return buf.getvalue()


def test_image_detail_defaults():
    assert settings.pass1_image_detail == "low"
    assert settings.vision_image_detail == "high"
    assert settings.pass1_rescue_detail == "high"


# ---- partial names backed by evidence --------------------------------------

def test_partial_overlap_needs_two_words_not_one_common_first_name():
    assert _partial_name_overlap("Fa1zan Syed", "Syed Faizan")           # OCR slip, both words line up
    assert _partial_name_overlap("Mohd Al Rashid", "Mohammed Rashid Al")
    assert not _partial_name_overlap("Ahmed Yusuf", "Ahmed Khan")        # one shared first name only
    assert not _partial_name_overlap("Sara Khan", "Sana Khan")           # one shared surname only
    assert _partial_name_overlap("Faizan", "Syed Faizan")                # a one-word name, the word agrees


async def _employees(*rows):
    async with SessionLocal() as db:
        for r in rows:
            db.add(Employee(**r))
        await db.commit()


async def _drop_employees(*ids):
    from sqlalchemy import delete
    async with SessionLocal() as db:
        await db.execute(delete(Employee).where(Employee.id.in_(ids)))
        await db.commit()


async def test_a_partial_name_backed_by_the_printed_id_is_matched_but_flagged():
    await _employees(dict(id="wn-1", employee_id="WN100", name="Syed Faizan Uddin", active=True))
    try:
        async with SessionLocal() as db:
            m = await matching.match_employee(db, "WN100", "Sayed Fayzan", sender_email="boss@client.com", corroborate=True)
        assert m.employee is not None and m.employee.id == "wn-1"
        assert m.code == MatchCode.ID_PARTIAL_NAME and m.low_confidence
        assert "Low confidence" in m.note and "Verify" in m.note
    finally:
        await _drop_employees("wn-1")


async def test_a_partial_name_backed_by_the_senders_own_address_is_matched_but_flagged():
    await _employees(dict(id="wn-2", employee_id="WN200", name="Syed Faizan Uddin", active=True,
                          work_email="faizan.syed@alpha.ae"))
    try:
        async with SessionLocal() as db:
            m = await matching.match_employee(db, None, "Sayed Fayzan", sender_email="faizan.syed@alpha.ae", corroborate=True)
        assert m.employee is not None and m.employee.id == "wn-2"
        assert m.code == MatchCode.EMAIL_PARTIAL_NAME and m.low_confidence
    finally:
        await _drop_employees("wn-2")


async def test_nothing_is_guessed_when_the_evidence_is_weak_or_disagrees():
    await _employees(
        dict(id="wn-3", employee_id="WN300", name="Ahmed Khan", active=True),
        dict(id="wn-4", employee_id="WN400", name="Sana Khan", active=True, work_email="sana@alpha.ae"),
        dict(id="wn-7", employee_id="WN700", name="Syed Faizan Uddin", active=True),
    )
    try:
        async with SessionLocal() as db:
            # the ID is Ahmed's but only the first name is shared -> no match
            assert (await matching.match_employee(db, "WN300", "Ahmed Yusuf",
                                                   sender_email="x@y.com", corroborate=True)).employee is None
            # the ID points at one person and the sender's address at another -> no guess
            res = await matching.match_employee(db, "WN700", "Sayed Fayzan", sender_email="x@y.com", corroborate=True)
            assert res.employee is not None and res.employee.id == "wn-7" and res.low_confidence   # ID alone backs it
            # an unknown ID and an unknown sender -> still unassigned, with the usual note
            res = await matching.match_employee(db, "NOPE", "Sayed Fayzan", sender_email="z@z.com", corroborate=True)
            assert res.employee is None and not res.low_confidence
    finally:
        await _drop_employees("wn-3", "wn-4", "wn-7")


async def test_callers_without_a_sender_behave_exactly_as_before():
    await _employees(dict(id="wn-5", employee_id="WN500", name="Syed Faizan Uddin", active=True))
    try:
        async with SessionLocal() as db:
            res = await matching.match_employee(db, "WN500", "Sayed Fayzan")     # bulk roster / rematch style call
        assert res.employee is None and not res.low_confidence
    finally:
        await _drop_employees("wn-5")


async def test_a_low_confidence_match_is_a_blocker_on_the_group():
    await _employees(dict(id="wn-6", employee_id="WN600", name="Syed Faizan Uddin", active=True))
    sheet = {"kind": "timesheet", "name": "w.pdf", "employee_name": "Sayed Fayzan", "employee_id": "WN600",
             "month": 6, "year": 2026, "period_type": "week", "days_covered": 3, "missing_days": [],
             "working_days": ["2026-06-01", "2026-06-02", "2026-06-03"], "weekend_days": [],
             "uncertain_days": [], "annual": [], "remote": [], "sick": [], "maternity": [],
             "unpaid": [], "absent": [], "public_holiday": [], "other": [], "notes": ""}
    email = SimpleNamespace(sender_email="boss@client.com", body_text="")
    try:
        async with SessionLocal() as db:
            (g,) = await group_sheets(db, email, [sheet])
        assert g["employee_pk"] == "wn-6"
        assert any("Low confidence" in i for i in g["issues"]), g["issues"]
    finally:
        await _drop_employees("wn-6")
