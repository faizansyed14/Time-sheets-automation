"""Timesheet filename hints for inbox AI check."""
from app.services.inbox.timesheet_detect import (
    extract_id_from_filename,
    filename_timesheet_hint,
)


def test_signed_attendance_is_timesheet():
    cat, _ = filename_timesheet_hint("ATTENDANCE_SHEE_E2507237_202605 (part 1) - signed.pdf")
    assert cat == "timesheet"


def test_audit_trail_is_other():
    cat, reason = filename_timesheet_hint("ATTENDANCE_SHEE_E2507237_202605 - audit.pdf")
    assert cat == "other"
    assert "audit" in reason.lower()


def test_extract_id_from_filename():
    assert extract_id_from_filename("ATTENDANCE_SHEE_E2507237_202605 - audit.pdf") == "E2507237"
