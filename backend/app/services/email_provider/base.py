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
    cid: str | None = None  # MIME Content-ID for inline CID resolution


@dataclass
class ProviderMessage:
    message_id: str
    sender_name: str
    sender_email: str
    subject: str
    received_at: datetime
    body_text: str
    body_html: str | None = None
    attachments: list[ProviderAttachment] = field(default_factory=list)


class EmailProvider(ABC):
    """Read-only view over a mailbox/folder."""

    @abstractmethod
    async def list_messages(
        self, query: str | None = None, since: datetime | None = None,
    ) -> list[ProviderMessage]:
        """List messages, newest first. `since` narrows to messages received
        after that time — the incremental-sync fast path: one light request
        instead of re-downloading the whole folder."""
        ...

    @abstractmethod
    async def get_message(self, message_id: str) -> ProviderMessage | None:
        ...

    @abstractmethod
    async def get_attachment_bytes(self, message_id: str, attachment_id: str) -> tuple[bytes, str, str]:
        """Return (bytes, filename, content_type)."""
        ...

    async def get_message_mime(self, message_id: str) -> bytes | None:
        """Full raw RFC-822 MIME of the message, when the provider supports it
        (Graph: GET /messages/{id}/$value). None → caller reconstructs the .eml
        from the stored fields + attachments instead."""
        return None
