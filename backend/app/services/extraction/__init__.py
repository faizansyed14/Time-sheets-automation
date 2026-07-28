"""Extraction building blocks.

Extract Email lives in services/extract_email/ — the single path every entry
point uses (Extract Email, Upload page, chat). It runs against a vision model
with no fallback engine, so there is no engine factory here; the modules in
this package are its shared helpers: file_processor (page images / type
detection), eml_parser, ocr, parser, validation and vision_client.
"""
from __future__ import annotations
