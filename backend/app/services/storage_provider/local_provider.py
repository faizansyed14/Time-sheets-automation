"""Local filesystem storage provider (default)."""
from __future__ import annotations

import mimetypes
import re
import shutil
from pathlib import Path

from app.core.config import settings
from app.services.storage_provider.base import (
    EmployeeFolder,
    FileItem,
    MonthFolder,
    StorageProvider,
)


def _safe(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r'[<>:"/\\|?*]+', "_", name)
    return name or "Unknown"


class LocalStorageProvider(StorageProvider):
    @property
    def root(self) -> Path:
        return settings.storage_path

    def _abs(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("Path escapes storage root")
        return p

    # ---- listing ----
    def list_employees(self) -> list[EmployeeFolder]:
        out = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            if d.is_dir():
                out.append(EmployeeFolder(name=d.name, rel_path=d.name,
                                          month_count=sum(1 for m in d.iterdir() if m.is_dir())))
        return out

    def list_months(self, employee: str) -> list[MonthFolder]:
        base = self._abs(_safe(employee))
        out = []
        if base.exists():
            for d in sorted(base.iterdir()):
                if d.is_dir():
                    out.append(MonthFolder(name=d.name, rel_path=f"{_safe(employee)}/{d.name}",
                                           file_count=sum(1 for f in d.iterdir() if f.is_file())))
        return out

    def list_items(self, employee: str, month: str) -> list[FileItem]:
        base = self._abs(f"{_safe(employee)}/{_safe(month)}")
        out = []
        if base.exists():
            for f in sorted(base.iterdir()):
                if f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    out.append(FileItem(name=f.name, rel_path=f"{_safe(employee)}/{_safe(month)}/{f.name}",
                                        size=f.stat().st_size, content_type=ctype))
        return out

    # ---- reading ----
    def read_file(self, rel_path: str) -> tuple[bytes, str, str]:
        p = self._abs(rel_path)
        if not p.is_file():
            raise FileNotFoundError(rel_path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        return p.read_bytes(), p.name, ctype

    # ---- writing ----
    def save_file(self, employee: str, month_label: str, filename: str, data: bytes) -> str:
        folder = self._abs(f"{_safe(employee)}/{_safe(month_label)}")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / _safe(filename)
        dest.write_bytes(data)
        return str(dest.relative_to(self.root))

    def save_text(self, employee: str, month_label: str, filename: str, text: str) -> str:
        folder = self._abs(f"{_safe(employee)}/{_safe(month_label)}")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / _safe(filename)
        dest.write_text(text)
        return str(dest.relative_to(self.root))

    # ---- folder CRUD ----
    def create_employee(self, name: str) -> EmployeeFolder:
        d = self._abs(_safe(name))
        d.mkdir(parents=True, exist_ok=True)
        return EmployeeFolder(name=d.name, rel_path=d.name, month_count=0)

    def create_month(self, employee: str, month_label: str) -> MonthFolder:
        d = self._abs(f"{_safe(employee)}/{_safe(month_label)}")
        d.mkdir(parents=True, exist_ok=True)
        return MonthFolder(name=d.name, rel_path=f"{_safe(employee)}/{d.name}", file_count=0)

    def rename_folder(self, rel_path: str, new_name: str) -> str:
        src = self._abs(rel_path)
        if not src.is_dir():
            raise FileNotFoundError(rel_path)
        dst = src.parent / _safe(new_name)
        src.rename(dst)
        return str(dst.relative_to(self.root))

    def delete_folder(self, rel_path: str) -> None:
        p = self._abs(rel_path)
        if p.is_dir():
            shutil.rmtree(p)

    def delete_file(self, rel_path: str) -> None:
        p = self._abs(rel_path)
        if p.is_file():
            p.unlink()
