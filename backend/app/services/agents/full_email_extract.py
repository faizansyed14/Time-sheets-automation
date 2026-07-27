"""
Backward-compatible facade for Extract Email.

Prefer importing from app.services.extract_email directly.
"""
from app.services.extract_email import (
    BUCKETS as _BUCKETS,
    TAG_PREFIX as _TAG_PREFIX,
    SourceCtx as _SourceCtx,
    analyse_upload,
    build_result as _result,
    extract_full_email,
    extract_upload,
    group_sheets as _group_sheets,
    mark_no_sheets as _mark_no_sheets,
    preview_llm_egress,
    stage_groups as _stage_groups,
    staged_message as _staged_message,
)

__all__ = [
    "_BUCKETS",
    "_TAG_PREFIX",
    "_SourceCtx",
    "_group_sheets",
    "_mark_no_sheets",
    "_result",
    "_stage_groups",
    "_staged_message",
    "analyse_upload",
    "extract_full_email",
    "extract_upload",
    "preview_llm_egress",
]
