"""Factory: returns the configured email provider."""
from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.email_provider.base import EmailProvider
from app.services.email_provider.mock_provider import MockEmailProvider


@lru_cache
def get_email_provider() -> EmailProvider:
    if settings.email_provider == "graph":
        from app.services.email_provider.graph_provider import GraphEmailProvider
        return GraphEmailProvider()
    return MockEmailProvider()
