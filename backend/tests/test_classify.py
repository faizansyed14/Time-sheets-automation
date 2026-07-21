"""Unit tests for classify parse + incompleteness wiring (no live OpenAI)."""
from app.services.extract_email.classify import (
    ClassifyResult,
    apply_classify_to_unit,
    fallback_classify,
    parse_classify_payload,
)
from app.services.extract_email.sheet_normalizer import normalize_sheet
from app.services.extract_email.types import SheetUnit
from app.services.extract_email import auto_accept


def test_parse_classify_incomplete_timesheet():
    r = parse_classify_payload({
        "format_id": "alpha_adr_attendance",
        "kind": "timesheet",
        "month": 6,
        "year": 2026,
        "expected_day_count": 30,
        "observed_day_count": 25,
        "dates_complete": False,
        "missing_days": [26, 27, 28, 29, 30],
        "confidence": "high",
    })
    assert r.format_id == "alpha_adr_attendance"
    assert r.dates_complete is False
    assert r.missing_days == [26, 27, 28, 29, 30]


def test_parse_classify_unknown_format_falls_back_generic():
    r = parse_classify_payload({"format_id": "not_a_real_format", "kind": "timesheet"})
    assert r.format_id == "generic"


def test_parse_classify_leave_certificate_forces_format():
    r = parse_classify_payload({"format_id": "bogus", "kind": "leave_certificate"})
    assert r.format_id == "leave_certificate"
    assert r.kind == "leave_certificate"


def test_normalize_carries_incomplete_flag():
    unit = SheetUnit("sheet.pdf", "pdf", b"%PDF", text="ATTENDANCE SHEET")
    apply_classify_to_unit(unit, ClassifyResult(
        format_id="alpha_adr_attendance", kind="timesheet",
        month=6, year=2026, expected_day_count=30, observed_day_count=20,
        dates_complete=False, missing_days=[21, 22], confidence="medium",
        source="llm",
    ))
    sheet = normalize_sheet(unit, {
        "kind": "timesheet", "employee_name": "A", "employee_id": "E1",
        "month": 6, "year": 2026,
    })
    assert sheet["incomplete_sheet"] is True
    assert sheet["dates_complete"] is False
    assert sheet["missing_days"] == [21, 22]
    assert sheet["format_id"] == "alpha_adr_attendance"


def test_auto_accept_blocked_by_incomplete_sheet():
    group = {
        "employee_pk": "pk-1",
        "name": "Test",
        "employee_id": "E1",
        "month": 6,
        "year": 2026,
        "format_id": "alpha_adr_attendance",
        "buckets": {b: [] for b in (
            "annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")},
        "overlap_flags": [],
        "fold_notes": [],
        "sheets": [{
            "name": "sheet.pdf",
            "kind": "timesheet",
            "format_id": "alpha_adr_attendance",
            "incomplete_sheet": True,
            "dates_complete": False,
            "missing_days": [30],
            "text": "\n".join(f"{d}-June-26 present" for d in range(1, 30)),
        }],
    }
    # Patch format recognition via sheets format_id — evaluate looks at format_id on sheets
    d = auto_accept.evaluate(group)
    assert d.accepted is False
    assert any("incomplete" in b.lower() for b in d.blockers)


def test_fallback_classify_adr_markers():
    unit = SheetUnit(
        "ts.pdf", "pdf", b"x",
        text=("ATTENDANCE SHEET\nEMP NO : E2406747 NAME: Albaraa\n"
              "SECTION: ADR MONTH: May YEAR: 2026\nMANAGER SIGNATURE"),
    )
    r = fallback_classify(unit)
    assert r.format_id == "alpha_adr_attendance"
    assert r.source == "fallback"
