"""Admin-configured month calendars (weekends + public holidays) fed into
Pass 2 as ground truth — see thread_prompt._calendar_block/_parse_period_hint
and pass2_blocks' per-item injection.
"""
from app.services.extract_email.thread_extract import Item
from app.services.extract_email.thread_prompt import (
    _calendar_block,
    _parse_period_hint,
    pass2_blocks,
)
from app.services.extract_email.grouping import calendar_mismatch_flags


def test_parse_period_hint_month_name_and_year():
    assert _parse_period_hint("June 2026") == (6, 2026)
    assert _parse_period_hint("Jun 2026") == (6, 2026)
    assert _parse_period_hint("  december 2025 ") == (12, 2025)


def test_parse_period_hint_numeric_forms():
    assert _parse_period_hint("2026-06") == (6, 2026)
    assert _parse_period_hint("06/2026") == (6, 2026)


def test_parse_period_hint_unparseable_returns_none():
    assert _parse_period_hint("") is None
    assert _parse_period_hint("last month sometime") is None


def test_calendar_block_renders_day_count_weekday_line_and_configured_data():
    row = {"weekend_weekdays": ["Friday", "Saturday"],
           "public_holidays": [{"date": "2026-06-15", "name": "Eid al-Adha"}]}
    block = _calendar_block(6, 2026, row)
    assert "CALENDAR FOR June 2026" in block
    assert "This month has 30 calendar days" in block
    # June 1 2026 is a Monday.
    assert "1=Monday" in block
    assert "2026-06-15" in block
    assert "Eid al-Adha" in block
    # Every Friday/Saturday in June 2026 must be listed as a weekend date.
    assert "2026-06-06" in block  # a Saturday
    assert "2026-06-05" in block  # a Friday


def test_calendar_block_without_admin_row_still_has_day_count():
    block = _calendar_block(7, 2026, None)
    assert "This month has 31 calendar days" in block
    assert "NOT configured" in block
    assert "NONE configured" in block


def test_calendar_block_with_no_holidays_says_none_configured():
    row = {"weekend_weekdays": ["Saturday", "Sunday"], "public_holidays": []}
    block = _calendar_block(1, 2026, row)
    assert "NONE configured" in block
    assert "Weekend dates this month (from Admin" in block


def _pair(source_key: str, name: str, period_hint: str, *, with_images: bool = False,
          with_text: bool = True):
    it = Item(key=source_key, name=name, mime="application/pdf", msg_index=0,
              text=("full OCR dump " * 50) if with_text else "",
              images=[b"fake-jpeg-bytes"] if with_images else [])
    meta = {"source": f"[{source_key}]", "kind": "timesheet", "employee_name": "Test Person",
            "employee_id": "E1", "period_hint": period_hint}
    return it, meta


def test_pass2_blocks_always_injects_calendar_when_period_hint_parses():
    """Even with no admin row for that month, days-in-month still goes in."""
    pairs = [_pair("A1", "june.pdf", "June 2026")]
    blocks = pass2_blocks(pairs, calendars={})
    texts = [b["text"] for b in blocks if b.get("type") == "text"]
    assert any("CALENDAR FOR June 2026" in t for t in texts)
    assert any("This month has 30 calendar days" in t for t in texts)


def test_pass2_blocks_includes_admin_weekends_when_configured():
    pairs = [_pair("A1", "june.pdf", "June 2026")]
    calendars = {(6, 2026): {"weekend_weekdays": ["Friday", "Saturday"], "public_holidays": []}}
    blocks = pass2_blocks(pairs, calendars=calendars)
    texts = [b["text"] for b in blocks if b.get("type") == "text"]
    assert any("2026-06-05" in t and "from Admin" in t for t in texts)


def test_pass2_blocks_omits_calendar_when_period_hint_unparseable():
    pairs = [_pair("A1", "sheet.pdf", "sometime recently")]
    calendars = {(6, 2026): {"weekend_weekdays": ["Friday", "Saturday"], "public_holidays": []}}
    blocks = pass2_blocks(pairs, calendars=calendars)
    texts = [b["text"] for b in blocks if b.get("type") == "text"]
    assert not any("CALENDAR FOR June 2026" in t for t in texts)


def test_pass2_blocks_gives_each_item_its_own_month_calendar():
    """A batch mixing two different months' sheets must get two DIFFERENT
    calendar blocks, each next to its own item, not one global block."""
    pairs = [_pair("A1", "june.pdf", "June 2026"), _pair("A2", "july.pdf", "July 2026")]
    calendars = {
        (6, 2026): {"weekend_weekdays": ["Friday", "Saturday"], "public_holidays": []},
        (7, 2026): {"weekend_weekdays": ["Saturday", "Sunday"], "public_holidays": []},
    }
    blocks = pass2_blocks(pairs, calendars=calendars)
    texts = [b["text"] for b in blocks if b.get("type") == "text"]
    assert any("CALENDAR FOR June 2026" in t for t in texts)
    assert any("CALENDAR FOR July 2026" in t for t in texts)


def test_pass2_skips_full_ocr_text_when_page_images_present():
    pairs = [_pair("A1", "sheet.pdf", "July 2026", with_images=True, with_text=True)]
    blocks = pass2_blocks(pairs, calendars={})
    texts = [b["text"] for b in blocks if b.get("type") == "text"]
    assert not any("TEXT CONTENT OF [A1]" in t for t in texts)
    assert not any("full OCR dump" in t for t in texts)
    assert any(b.get("type") == "image_url" for b in blocks)


def test_pass2_sends_text_when_no_images():
    pairs = [_pair("A1", "body-grid.txt", "July 2026", with_images=False, with_text=True)]
    blocks = pass2_blocks(pairs, calendars={})
    texts = [b["text"] for b in blocks if b.get("type") == "text"]
    assert any("TEXT CONTENT OF [A1]" in t and "full OCR dump" in t for t in texts)


def test_calendar_mismatch_flags_weekend_marked_working():
    sheets = [{
        "kind": "timesheet",
        "weekend_days": [],
        "working_days": ["2026-07-03", "2026-07-04"],  # Fri/Sat if admin Fri-Sat
        "public_holiday": [],
        "sick": [], "annual": [], "remote": [], "maternity": [],
        "unpaid": [], "absent": [],
    }]
    # July 2026: Fri=3, Sat=4
    flags = calendar_mismatch_flags(7, 2026, sheets, {
        "weekend_weekdays": ["Friday", "Saturday"],
        "public_holidays": [],
    })
    assert any("marked as working" in f for f in flags)


def test_calendar_mismatch_flags_missing_admin_ph():
    sheets = [{
        "kind": "timesheet",
        "weekend_days": [],
        "working_days": ["2026-07-15"],
        "public_holiday": [],
        "sick": [], "annual": [], "remote": [], "maternity": [],
        "unpaid": [], "absent": [],
    }]
    flags = calendar_mismatch_flags(7, 2026, sheets, {
        "weekend_weekdays": [],
        "public_holidays": [{"date": "2026-07-15", "name": "Something"}],
    })
    assert any("public holiday" in f.lower() and "missing" in f.lower() for f in flags)


def test_calendar_mismatch_flags_clean_when_matching():
    sheets = [{
        "kind": "timesheet",
        "weekend_days": ["2026-07-03", "2026-07-04"],
        "working_days": ["2026-07-01", "2026-07-02"],
        "public_holiday": ["2026-07-15"],
        "sick": [], "annual": [], "remote": [], "maternity": [],
        "unpaid": [], "absent": [],
    }]
    flags = calendar_mismatch_flags(7, 2026, sheets, {
        "weekend_weekdays": ["Friday", "Saturday"],
        "public_holidays": [{"date": "2026-07-15", "name": "PH"}],
    })
    assert flags == []
