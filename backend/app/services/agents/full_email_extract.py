"""
Backward-compatible facade for Extract Email.

Prefer importing from app.services.extract_email directly.
"""
from app.services.extract_email import (
    TAG_PREFIX as _TAG_PREFIX,
    analyse_upload,
    extract_full_email,
    extract_upload,
    group_sheets as _group_sheets,
    stage_groups as _stage_groups,
)

__all__ = [
    "_TAG_PREFIX",
    "_group_sheets",
    "_stage_groups",
    "analyse_upload",
    "extract_full_email",
    "extract_upload",
]
