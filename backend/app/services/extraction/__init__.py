"""Factory: the deterministic fallback engine.

The REAL extraction pipeline is services/agents/full_email_extract.py -- the
single path every entry point uses (Extract Email, selected attachments,
Upload page, chat). This engine only serves as its per-sheet fallback when a
vision batch call fails, and as the $0 keyless engine for dev/tests.
"""
from __future__ import annotations

from functools import lru_cache

from app.services.extraction.base import ExtractionEngine
from app.services.extraction.mock_engine import MockExtractionEngine


@lru_cache
def get_extraction_engine() -> ExtractionEngine:
    return MockExtractionEngine()
