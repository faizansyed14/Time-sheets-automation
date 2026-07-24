"""
Extract Email — shared pipeline for inbox, upload, and chat.

Flow:
  collect units → analyse (vision) → detect approval → group → stage
"""
from app.services.extract_email.analyser import analyse_units, is_native_file_unit
from app.services.extract_email.approval import detect_approval
from app.services.extract_email.collector import (
    collect_units,
    content_hash,
    merge_thread_units,
    unit_from_bytes,
)
from app.services.extract_email.constants import BUCKETS, MAX_SHEETS, TAG_PREFIX
from app.services.extract_email.email import extract_full_email
from app.services.extract_email.grouping import group_sheets, union_group_buckets
from app.services.extract_email.preview import preview_llm_egress
from app.services.extract_email.prompts import extract_prompt, system_prompt
from app.services.extract_email.results import build_result, staged_message
from app.services.extract_email.sheet_normalizer import (
    boost_sheet_from_hints,
    clean_dates,
    infer_from_filename,
    normalize_sheet,
    sanitize_body_sheet,
)
from app.services.extract_email.staging import mark_no_sheets, stage_groups
from app.services.extract_email.types import SheetUnit, SourceCtx
from app.services.extract_email.upload import analyse_upload, extract_upload, units_from_upload

__all__ = [
    "BUCKETS",
    "MAX_SHEETS",
    "TAG_PREFIX",
    "SheetUnit",
    "SourceCtx",
    "analyse_upload",
    "analyse_units",
    "boost_sheet_from_hints",
    "build_result",
    "clean_dates",
    "collect_units",
    "content_hash",
    "detect_approval",
    "extract_full_email",
    "extract_prompt",
    "extract_upload",
    "group_sheets",
    "infer_from_filename",
    "is_native_file_unit",
    "merge_thread_units",
    "mark_no_sheets",
    "normalize_sheet",
    "preview_llm_egress",
    "sanitize_body_sheet",
    "stage_groups",
    "staged_message",
    "system_prompt",
    "union_group_buckets",
    "unit_from_bytes",
    "units_from_upload",
]
