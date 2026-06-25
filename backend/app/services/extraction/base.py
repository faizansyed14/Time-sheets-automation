"""
Extraction engine abstraction.

The ingestion pipeline depends only on this interface. Today it resolves to the
mock engine; later set extraction_engine="vision" and drop your real
GPT-4o/vision parser behind the same two methods.
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
    # validation model, embedded .eml attachment, etc.
    extraction_meta: dict = field(default_factory=dict)


@dataclass
class ApprovalExtraction:
    detected: bool
    detail: str


class ExtractionEngine(ABC):
    @abstractmethod
    async def extract_timesheet(
        self, data: bytes, filename: str, content_type: str,
        message_id: str, attachment_id: str,
    ) -> TimesheetExtraction:
        ...

    @abstractmethod
    async def extract_approval(
        self, data: bytes, message_id: str, attachment_id: str,
    ) -> ApprovalExtraction:
        ...

    async def summarize(self, context: dict) -> str | None:
        """Optional: produce a polished plain-English review summary for a whole
        month's record (after files are merged and validated). Return None to
        let the pipeline fall back to the deterministic summarizer.

        context = {employee, month, year, leaves: {bucket: [iso dates]},
                   issues: [str], n_files: int}"""
        return None
