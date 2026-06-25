"""
Archive export — build a ZIP of the storage tree, backend-agnostic.

Walks the active StorageProvider (local disk, S3, …) via its
managers → employees → months → items interface and streams each file into a
ZIP, so download works the same whether files live on disk or in S3.

  build_zip(manager=None) -> bytes
    • manager=None  → the entire store
    • manager="X"   → just that manager's subtree
"""
from __future__ import annotations

import io
import zipfile

from app.services.storage_provider import get_storage_provider


def build_zip(manager: str | None = None) -> bytes:
    """Build a ZIP of the vault. Uses the provider's iter_files(), which on S3
    does a single bulk listing + parallel reads (and on local a single rglob),
    so large archives export quickly instead of one round-trip per folder/file."""
    sp = get_storage_provider()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for zip_path, data in sp.iter_files(manager):
            zf.writestr(zip_path, data)
    buf.seek(0)
    return buf.read()
