"""
Extraction engine abstraction.

Per-sheet fallback when the shared vision pipeline cannot read a file.
Today resolves to the mock/deterministic engine; production uses
full_email_extract + vision_client for the main path.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TimesheetExtraction:
    employee_id: str | None
    employee_name: str | None
    month: int
    year: int
    annual_leave_dates: list[str] = field(default_factory=list)
    remote_work_dates: list[str] = field(default_factory=list)
    sick_leave_dates: list[str] = field(default_factory=list)
    maternity_leave_dates: list[str] = field(default_factory=list)
    unpaid_leave_dates: list[str] = field(default_factory=list)
    absent_dates: list[str] = field(default_factory=list)
    public_holiday_dates: list[str] = field(default_factory=list)
    validation_status: str = "verified"   # verified | manual_review
    summary: str = ""
    hr_flags: list[str] = field(default_factory=list)
    # ---- cost / provenance (surfaced on the pipeline tracker) ----
    # Which engine path produced this result, so reviewers can see (and cost-
    # control) what was used per file:
    #   extraction_model  : the LLM model id (e.g. "gpt-4o", "gpt-4o-mini") or
    #                       None when no LLM was called (deterministic / mock).
    #   extraction_method : "vision-llm" | "deterministic-text" | "mock" |
    #                       "unsupported"
    #   used_ocr          : True when the local OCR reader produced the text layer.
    extraction_model: str | None = None
    extraction_method: str | None = None
    used_ocr: bool = False
    # Free-form provenance shown in the tracker's "Extraction details" dropdown:
    # render DPI, image detail, page count, OCR provider, text-layer presence,
    # embedded .eml attachment, etc.
    extraction_meta: dict = field(default_factory=dict)


class ExtractionEngine(ABC):
    """Deterministic per-sheet fallback interface. Approval detection and
    summaries live in the shared pipeline (full_email_extract), not here."""

    @abstractmethod
    async def extract_timesheet(
        self, data: bytes, filename: str, content_type: str,
        message_id: str, attachment_id: str,
    ) -> TimesheetExtraction:
        ...
