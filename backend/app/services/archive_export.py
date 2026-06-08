"""
Archive export service — build ZIP files from the storage tree.

build_zip(manager=None) -> bytes
  • manager=None  → zip the ENTIRE store (all managers, employees, months, files)
  • manager="X"   → zip just that manager's subtree

Streams via ZipFile to avoid loading everything into memory at once.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.core.config import settings


def _root() -> Path:
    return settings.storage_path


def build_zip(manager: str | None = None) -> bytes:
    """Return the ZIP archive as bytes. Uses streaming ZipFile with DEFLATED compression."""
    buf = io.BytesIO()
    root = _root()

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        if manager:
            # Only one manager's subtree
            mgr_path = root / manager
            if mgr_path.is_dir():
                _add_tree(zf, mgr_path, root)
        else:
            # Whole tree
            if root.exists():
                _add_tree(zf, root, root)

    buf.seek(0)
    return buf.read()


def _add_tree(zf: zipfile.ZipFile, directory: Path, base: Path) -> None:
    """Recursively add all files under `directory` to the zip, paths relative to `base`."""
    for item in sorted(directory.rglob("*")):
        if item.is_file():
            arcname = str(item.relative_to(base))
            zf.write(item, arcname=arcname)
