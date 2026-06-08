"""Factory + thin helpers for the active storage provider."""
from __future__ import annotations

import calendar
from functools import lru_cache

from app.core.config import settings
from app.services.storage_provider.base import StorageProvider

_UNASSIGNED = "Unassigned"


@lru_cache
def get_storage_provider() -> StorageProvider:
    if settings.storage_provider == "onedrive":
        from app.services.storage_provider.onedrive_provider import OneDriveStorageProvider
        return OneDriveStorageProvider()
    from app.services.storage_provider.local_provider import LocalStorageProvider
    return LocalStorageProvider()


def month_label(month: int, year: int) -> str:
    mname = calendar.month_name[month] if 1 <= month <= 12 else f"M{month}"
    return f"{mname}-{year}"


def _safe(name: str) -> str:
    """Mirror _safe from local_provider without circular import."""
    import re
    name = (name or "").strip()
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    return name or "Unknown"


# ---- helpers used by the ingestion pipeline ----
def save_file(manager: str, employee: str, month: int, year: int, filename: str, data: bytes) -> str:
    return get_storage_provider().save_file(
        manager or _UNASSIGNED, employee, month_label(month, year), filename, data
    )


def save_text(manager: str, employee: str, month: int, year: int, filename: str, text: str) -> str:
    return get_storage_provider().save_text(
        manager or _UNASSIGNED, employee, month_label(month, year), filename, text
    )


def folder_rel(manager: str, employee: str, month: int, year: int) -> str:
    mgr = manager or _UNASSIGNED
    get_storage_provider().create_month(mgr, employee, month_label(month, year))
    return f"{_safe(mgr)}/{_safe(employee)}/{_safe(month_label(month, year))}"
