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
