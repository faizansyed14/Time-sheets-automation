"""
Email provider abstraction.

The rest of the app only ever talks to this interface, so swapping the mock
for Microsoft Graph later is a one-line config change (email_provider="graph")
and zero changes anywhere else.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ProviderAttachment:
    attachment_id: str
    filename: str
    content_type: str
    size: int
    kind: str  # "timesheet" | "approval_screenshot" | "other"


@dataclass
class ProviderMessage:
    message_id: str
    sender_name: str
    sender_email: str
    subject: str
    received_at: datetime
    body_text: str
    attachments: list[ProviderAttachment] = field(default_factory=list)


class EmailProvider(ABC):
    """Read-only view over a mailbox/folder."""

    @abstractmethod
    async def list_messages(self, query: str | None = None) -> list[ProviderMessage]:
        ...

    @abstractmethod
    async def get_message(self, message_id: str) -> ProviderMessage | None:
        ...

    @abstractmethod
    async def get_attachment_bytes(self, message_id: str, attachment_id: str) -> tuple[bytes, str, str]:
        """Return (bytes, filename, content_type)."""
        ...
