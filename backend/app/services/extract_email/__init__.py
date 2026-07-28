"""
Extract Email — shared two-pass vision pipeline for inbox, upload, and chat.

Flow: collect thread (Item/Message/Thread) → pass 1 classify → pass 2 extract
→ group by employee+month → auto-accept gate → stage.
"""
from app.services.extract_email.constants import BUCKETS, TAG_PREFIX
from app.services.extract_email.email import extract_full_email
from app.services.extract_email.grouping import group_sheets
from app.services.extract_email.preview import preview_llm_egress
from app.services.extract_email.results import build_result, staged_message
from app.services.extract_email.staging import mark_no_sheets, stage_groups
from app.services.extract_email.types import SourceCtx
from app.services.extract_email.upload import analyse_upload, extract_upload

__all__ = [
    "BUCKETS",
    "TAG_PREFIX",
    "SourceCtx",
    "analyse_upload",
    "build_result",
    "extract_full_email",
    "extract_upload",
    "group_sheets",
    "mark_no_sheets",
    "preview_llm_egress",
    "stage_groups",
    "staged_message",
]
