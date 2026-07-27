"""Extract Email datatypes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SourceCtx:
    """Duck-typed stand-in for EmailMessage when the source is an uploaded file."""
    subject: str | None = None
    body_text: str | None = None
    sender_email: str | None = None
    provider_message_id: str | None = None
