"""
Storage provider abstraction.

The app talks only to this interface, so the file store can be swapped from
local disk to OneDrive/SharePoint (via Microsoft Graph) by changing one config
value — STORAGE_PROVIDER — with no other code changes.

Folder model (THREE levels):   <Account Manager> / <Employee Name> / <Month-Year> / <files>
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass
class FileItem:
    name: str
    rel_path: str
    size: int
    content_type: str


@dataclass
class MonthFolder:
    name: str            # e.g. "February-2026"
    rel_path: str        # "<manager>/<emp>/February-2026"
    file_count: int


@dataclass
class EmployeeFolder:
    name: str            # employee name
    rel_path: str        # "<manager>/<emp>"
    month_count: int


@dataclass
class ManagerFolder:
    name: str            # account manager name
    rel_path: str        # "<manager>"
    employee_count: int


class StorageProvider(ABC):
    # ---- listing ----
    @abstractmethod
    def list_managers(self) -> list[ManagerFolder]: ...

    @abstractmethod
    def list_employees(self, manager: str) -> list[EmployeeFolder]: ...

    @abstractmethod
    def list_months(self, manager: str, employee: str) -> list[MonthFolder]: ...

    @abstractmethod
    def list_items(self, manager: str, employee: str, month: str) -> list[FileItem]: ...

    # ---- reading ----
    @abstractmethod
    def read_file(self, rel_path: str) -> tuple[bytes, str, str]:
        """Return (bytes, filename, content_type)."""

    def iter_files(self, manager: str | None = None) -> Iterator[tuple[str, bytes]]:
        """Yield (zip_path, data) for every file in the vault (optionally scoped
        to one manager). Default implementation walks the 3-level listing; S3 /
        local override this with a single bulk listing + parallel reads, which is
        dramatically faster for large archives. zip_path is
        '<Manager>/<Employee>/<Month-Year>/<file>'."""
        managers = [m.name for m in self.list_managers()]
        if manager:
            managers = [m for m in managers if m == manager]
        for mgr in managers:
            for emp in self.list_employees(mgr):
                for mon in self.list_months(mgr, emp.name):
                    for item in self.list_items(mgr, emp.name, mon.name):
                        try:
                            data, _name, _ctype = self.read_file(item.rel_path)
                        except Exception:
                            continue
                        yield f"{mgr}/{emp.name}/{mon.name}/{item.name}", data

    def iter_file_meta(self, manager: str | None = None) -> Iterator[tuple[str, int]]:
        """Yield (zip_path, size_bytes) for every vault file WITHOUT downloading
        it — metadata only. Used to compute available years and the total size of
        a download up front (so the UI can show an accurate progress bar). Local
        and S3 override this with a single cheap listing; this default falls back
        to the 3-level walk."""
        managers = [m.name for m in self.list_managers()]
        if manager:
            managers = [m for m in managers if m == manager]
        for mgr in managers:
            for emp in self.list_employees(mgr):
                for mon in self.list_months(mgr, emp.name):
                    for item in self.list_items(mgr, emp.name, mon.name):
                        yield f"{mgr}/{emp.name}/{mon.name}/{item.name}", int(item.size or 0)

    # ---- writing (used by the ingestion pipeline) ----
    @abstractmethod
    def save_file(self, manager: str, employee: str, month_label: str, filename: str, data: bytes) -> str: ...

    @abstractmethod
    def save_text(self, manager: str, employee: str, month_label: str, filename: str, text: str) -> str: ...

    # ---- folder CRUD (used by the Files page) ----
    @abstractmethod
    def create_manager(self, name: str) -> ManagerFolder: ...

    @abstractmethod
    def create_employee(self, manager: str, name: str) -> EmployeeFolder: ...

    @abstractmethod
    def create_month(self, manager: str, employee: str, month_label: str) -> MonthFolder: ...

    @abstractmethod
    def rename_folder(self, rel_path: str, new_name: str) -> str: ...

    @abstractmethod
    def delete_folder(self, rel_path: str) -> None: ...

    @abstractmethod
    def delete_file(self, rel_path: str) -> None: ...
