"""
Mock extraction engine.

Returns canned-but-coherent results tied to app.seed.mock_data, then runs the
SAME real validation pass (validation.validate) the production engine would, so
the summary / yellow flags are genuinely computed, not faked.
"""
from __future__ import annotations

import calendar

from app.seed import mock_data
from app.services.extraction.base import (
    ApprovalExtraction,
    ExtractionEngine,
    TimesheetExtraction,
)
from app.services.extraction.validation import validate


class MockExtractionEngine(ExtractionEngine):
    async def extract_timesheet(
        self, data: bytes, filename: str, content_type: str,
        message_id: str, attachment_id: str,
    ) -> TimesheetExtraction:
        case = mock_data.case_for_attachment(attachment_id)
        if not case:
            # Unknown attachment (e.g. a manual upload). The mock only knows the
            # seeded email cases; real uploads are read by the vision engine.
            return TimesheetExtraction(
                employee_id=None, employee_name=None, month=0, year=0,
                validation_status="manual_review",
                summary="Mock engine can't read ad-hoc uploads — set EXTRACTION_ENGINE=vision "
                        "to extract real files with your LLM.",
                hr_flags=["Mock engine: upload not recognised."],
            )

        raw = {
            "annual": case.get("annual", []),
            "remote": case.get("remote", []),
            "sick": case.get("sick", []),
            "unpaid": case.get("unpaid", []),
            "absent": case.get("absent", []),
            "public_holiday": case.get("public_holiday", []),
        }
        cleaned, flags = validate(
            raw, case["month"], case["year"],
            header_month=case.get("header_month"), header_year=case.get("header_year"),
        )

        status = "manual_review" if flags else "verified"
        if flags:
            summary = "Needs review: " + " ".join(flags)
        else:
            total = sum(len(v) for v in cleaned.values())
            mname = calendar.month_name[case["month"]]
            summary = f"Clean extraction — {total} leave/holiday day(s) for {mname} {case['year']}."

        return TimesheetExtraction(
            employee_id=case.get("emp_id") or None,
            employee_name=case.get("emp_name"),
            month=case["month"],
            year=case["year"],
            annual_leave_dates=cleaned["annual"],
            remote_work_dates=cleaned["remote"],
            sick_leave_dates=cleaned["sick"],
            unpaid_leave_dates=cleaned["unpaid"],
            absent_dates=cleaned["absent"],
            public_holiday_dates=cleaned["public_holiday"],
            validation_status=status,
            summary=summary,
            hr_flags=flags,
        )

    async def extract_approval(
        self, data: bytes, message_id: str, attachment_id: str,
    ) -> ApprovalExtraction:
        ap = mock_data.approval_for_message(message_id)
        if not ap:
            return ApprovalExtraction(detected=False, detail="No approval screenshot in this email.")
        return ApprovalExtraction(detected=bool(ap["detected"]), detail=ap["detail"])
