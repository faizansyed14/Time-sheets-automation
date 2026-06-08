"""Factory + thin helpers for the active storage provider."""
from __future__ import annotations

import calendar
from functools import lru_cache

from app.core.config import settings
from app.services.storage_provider.base import StorageProvider


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


# ---- helpers used by the ingestion pipeline ----
def save_file(employee: str, month: int, year: int, filename: str, data: bytes) -> str:
    return get_storage_provider().save_file(employee, month_label(month, year), filename, data)


def save_text(employee: str, month: int, year: int, filename: str, text: str) -> str:
    return get_storage_provider().save_text(employee, month_label(month, year), filename, text)


def folder_rel(employee: str, month: int, year: int) -> str:
    # ensure it exists, return its relative path
    get_storage_provider().create_month(employee, month_label(month, year))
    from app.services.storage_provider.local_provider import _safe
    return f"{_safe(employee)}/{_safe(month_label(month, year))}"
