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
    sp = get_storage_provider()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        managers = [m.name for m in sp.list_managers()]
        if manager:
            managers = [m for m in managers if m == manager]
        for mgr in managers:
            for emp in sp.list_employees(mgr):
                for mon in sp.list_months(mgr, emp.name):
                    for item in sp.list_items(mgr, emp.name, mon.name):
                        try:
                            data, _name, _ctype = sp.read_file(item.rel_path)
                        except Exception:
                            continue
                        zf.writestr(f"{mgr}/{emp.name}/{mon.name}/{item.name}", data)
    buf.seek(0)
    return buf.read()
