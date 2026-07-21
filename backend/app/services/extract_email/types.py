"""Extract Email datatypes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SheetUnit:
    """One analysable sheet found inside the full .eml."""
    name: str
    ftype: str
    payload: bytes
    images: list[bytes] = field(default_factory=list)
    text: str = ""
    # Detected / classified client-template id (see extract_email.formats).
    format_id: str = "generic"
    # Latest classify result (ClassifyResult) — set by classify_unit.
    classify: Any = None


@dataclass
class SourceCtx:
    """Duck-typed stand-in for EmailMessage when the source is an uploaded file."""
    subject: str | None = None
    body_text: str | None = None
    sender_email: str | None = None
    provider_message_id: str | None = None
