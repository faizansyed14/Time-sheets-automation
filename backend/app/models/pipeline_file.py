"""
PipelineFile — full audit trail of every file that enters the extraction
pipeline (from the Upload page OR an accepted email).

One row per timesheet file. The row walks through stages
(received → protection_check → extraction → identification → matching →
validation → filing → recorded) and ends in a terminal status:

  success       fully processed, record created/merged
  needs_review  record created but flagged (validation mismatch, ID/name
                disagreement, storage problem)
  failed        nothing was filed — failure_code says exactly where and why
                (protected_pdf, llm_failed, name_not_found, month_not_found,
                 employee_not_matched, ambiguous_id, unsupported_type, ...)
  resolved      a human pressed Resolve (or a Retry succeeded)

The original bytes are kept under storage/_pipeline/<id>/ so failed files can
be retried after the cause is fixed (e.g. employee added to the matcher).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class PipelineStatus:
    PROCESSING = "processing"
    SUCCESS = "success"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    RESOLVED = "resolved"


class PipelineStage:
    RECEIVED = "received"
    PROTECTION_CHECK = "protection_check"
    EXTRACTION = "extraction"
    IDENTIFICATION = "identification"
    MATCHING = "matching"
    VALIDATION = "validation"
    FILING = "filing"
    RECORDED = "recorded"

    ORDER = [RECEIVED, PROTECTION_CHECK, EXTRACTION, IDENTIFICATION,
             MATCHING, VALIDATION, FILING, RECORDED]


class FailureCode:
    PROTECTED_PDF = "protected_pdf"            # password-protected / encrypted PDF
    UNSUPPORTED_TYPE = "unsupported_type"      # not a pdf/docx/xlsx/image/eml
    EMPTY_FILE = "empty_file"
    LLM_FAILED = "llm_failed"                  # extraction engine raised (API/key/network)
    EXTRACTION_UNREADABLE = "extraction_unreadable"  # engine ran but got nothing usable
    NAME_NOT_FOUND = "name_not_found"          # no employee name/ID on the sheet
    MONTH_NOT_FOUND = "month_not_found"        # no usable month/year on the sheet
    EMPLOYEE_NOT_MATCHED = "employee_not_matched"  # identity not in the matcher list
    AMBIGUOUS_ID = "ambiguous_id"              # shared AUH/DXB id, name can't disambiguate
    ID_NAME_MISMATCH = "id_name_mismatch"      # id and name point to different people
    VALIDATION_MISMATCH = "validation_mismatch"  # duplicate/out-of-month/header mismatch flags
    STORAGE_ERROR = "storage_error"
    DUPLICATE_FILE = "duplicate_file"          # identical file already processed (no-op)
    PENDING_REVIEW = "pending_review"          # AI-extracted, awaiting human accept (not a failure)
    UNKNOWN = "unknown"


class PipelineFile(Base):
    __tablename__ = "pipeline_files"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)

    # ---- what came in ----
    filename: Mapped[str] = mapped_column(String)
    content_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_kind: Mapped[str] = mapped_column(String, index=True)  # "upload" | "email"
    # The message this was extracted FROM — still what retries, "mark email
    # ingested" and TimesheetRecord.source_email_id resolve against.
    source_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    # The CONVERSATION this belongs to. Extract Email reads a whole thread, so
    # this is the dedupe key: re-extracting after a new reply updates the same
    # review item instead of creating a second one for the same month.
    thread_key: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    attachment_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # ---- where it got to ----
    status: Mapped[str] = mapped_column(String, default=PipelineStatus.PROCESSING, index=True)
    stage: Mapped[str] = mapped_column(String, default=PipelineStage.RECEIVED)
    failure_code: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{"stage", "status": "ok"|"warn"|"fail", "detail", "at"}]
    events: Mapped[list] = mapped_column(JSON, default=list)

    # ---- what we learned about it ----
    employee_id: Mapped[str | None] = mapped_column(String, nullable=True)
    employee_name: Mapped[str | None] = mapped_column(String, nullable=True)
    month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    record_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    # ---- extraction provenance (cost visibility on the pipeline tracker) ----
    # Which GPT model handled this file (gpt-4o vs the cheaper gpt-4o-mini, etc.),
    # how it was read, and whether the local OCR reader was used.
    extraction_model: Mapped[str | None] = mapped_column(String, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String, nullable=True)
    used_ocr: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # render DPI, image detail, page count, OCR provider, text-layer presence,
    # embedded .eml attachment — shown in the UI dropdown.
    extraction_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # copy of the original bytes (relative to storage root) so Retry works
    raw_path: Mapped[str | None] = mapped_column(String, nullable=True)

    # ---- resolution ----
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
