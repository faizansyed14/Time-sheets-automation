"""Factory: returns the configured extraction engine."""
from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.extraction.base import ExtractionEngine
from app.services.extraction.mock_engine import MockExtractionEngine


@lru_cache
def get_extraction_engine() -> ExtractionEngine:
    if settings.extraction_engine == "vision":
        from app.services.extraction.vision_engine import VisionExtractionEngine
        return VisionExtractionEngine()
    return MockExtractionEngine()
