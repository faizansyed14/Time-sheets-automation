"""Factory + thin helpers for the active storage provider."""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone
from functools import lru_cache

from app.core.config import settings
from app.services.storage_provider.base import StorageProvider

_UNASSIGNED = "Unassigned"


@lru_cache
def get_storage_provider() -> StorageProvider:
    provider = (settings.storage_provider or "local").lower()
    if provider == "s3":
        from app.services.storage_provider.s3_provider import S3StorageProvider
        return S3StorageProvider()
    if provider == "onedrive":
        from app.services.storage_provider.onedrive_provider import OneDriveStorageProvider
        return OneDriveStorageProvider()
    from app.services.storage_provider.local_provider import LocalStorageProvider
    return LocalStorageProvider()


def month_label(month: int, year: int) -> str:
    mname = calendar.month_name[month] if 1 <= month <= 12 else f"M{month}"
    return f"{mname}-{year}"


def employee_folder_label(name: str, aco: str | None = None, dco: str | None = None) -> str:
    """The employee's File Vault folder name: "<Name> (ACO-1, DCO-2)".

    Whichever numbers exist are appended so a vault folder identifies the
    person by contract number, not just by a name that several people can
    share. With neither number it is the bare name — exactly the old
    behaviour, so employees without ACO/DCO keep their existing folder.
    """
    bits = [f"{label}-{v.strip()}" for label, v in (("ACO", aco), ("DCO", dco)) if (v or "").strip()]
    return f"{name} ({', '.join(bits)})" if bits else name


def employee_folder_base(folder_name: str) -> str:
    """Inverse of the label: the plain employee name a vault folder belongs to.

    "Jane Doe (ACO-1, DCO-2)" -> "Jane Doe";  "Jane Doe" -> "Jane Doe".
    Used to find a person's EXISTING folder when their ACO/DCO has since been
    filled in or corrected, so their history is renamed rather than split
    across two folders."""
    base = re.split(r"\s*\((?:ACO|DCO)[-\s:]", folder_name, maxsplit=1, flags=re.I)[0]
    return base.strip() or folder_name


def ensure_employee_folder(manager: str, name: str, aco: str | None, dco: str | None) -> str:
    """Folder name to file this employee under, migrating an older one if found.

    An employee whose ACO/DCO was added (or corrected) after their folder was
    created would otherwise get a SECOND folder and split their own history,
    so the existing folder is renamed to the new label instead. Best-effort:
    any rename failure just falls back to the new label rather than blocking
    the filing."""
    desired = employee_folder_label(name, aco, dco)
    mgr = manager or _UNASSIGNED
    try:
        existing = [e.name for e in get_storage_provider().list_employees(mgr)]
    except Exception:
        return desired
    if desired in existing:
        return desired
    base = employee_folder_base(desired)
    stale = [n for n in existing if n != desired and employee_folder_base(n) == base]
    if len(stale) == 1:
        try:
            get_storage_provider().rename_folder(f"{_safe(mgr)}/{_safe(stale[0])}", desired)
        except Exception:
            pass
    return desired


def _safe(name: str) -> str:
    """Mirror _safe from local_provider without circular import."""
    import re
    name = (name or "").strip()
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    return name or "Unknown"


def _dedupe_filename(manager: str, employee: str, month_lbl: str, filename: str) -> str:
    """If `filename` is already sitting in this vault folder, return a
    distinctly-dated alternative instead of letting save_file silently
    overwrite it.

    A thread re-extracted after a new reply builds a fresh bundle (the new
    message plus a couple of prior ones for context — never the full
    original history), under the SAME subject-derived filename as before.
    Without this, re-filing it would quietly replace whatever was already
    vaulted for that thread. Instead the update is stored alongside it, the
    way the new reply itself arrived separately — nothing already filed is
    ever touched."""
    try:
        existing = {item.name for item in get_storage_provider().list_items(manager, employee, month_lbl)}
    except Exception:
        return filename
    if filename not in existing:
        return filename
    stem, dot, ext = filename.rpartition(".")
    stem, ext = (stem, f".{ext}") if dot else (filename, "")
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidate = f"{stem} — {tag}{ext}"
    n = 2
    while candidate in existing:
        candidate = f"{stem} — {tag} ({n}){ext}"
        n += 1
    return candidate


# ---- helpers used by the ingestion pipeline ----
def save_file(manager: str, employee: str, month: int, year: int, filename: str, data: bytes) -> str:
    mgr = manager or _UNASSIGNED
    lbl = month_label(month, year)
    unique_name = _dedupe_filename(mgr, employee, lbl, filename)
    return get_storage_provider().save_file(mgr, employee, lbl, unique_name, data)


def save_text(manager: str, employee: str, month: int, year: int, filename: str, text: str) -> str:
    return get_storage_provider().save_text(
        manager or _UNASSIGNED, employee, month_label(month, year), filename, text
    )


def folder_rel(manager: str, employee: str, month: int, year: int) -> str:
    mgr = manager or _UNASSIGNED
    get_storage_provider().create_month(mgr, employee, month_label(month, year))
    return f"{_safe(mgr)}/{_safe(employee)}/{_safe(month_label(month, year))}"
