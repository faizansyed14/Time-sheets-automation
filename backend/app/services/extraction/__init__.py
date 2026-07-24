"""Factory: the deterministic fallback engine.

Extract Email lives in services/extract_email/ — the single path every
entry point uses (Extract Email, Upload page, chat). This engine only serves
as its per-sheet fallback when a vision extract call fails.
"""
from __future__ import annotations

from functools import lru_cache

from app.services.extraction.base import ExtractionEngine
from app.services.extraction.mock_engine import MockExtractionEngine


@lru_cache
def get_extraction_engine() -> ExtractionEngine:
    return MockExtractionEngine()
