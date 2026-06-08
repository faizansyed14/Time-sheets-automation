"""
Storage provider abstraction.

The app talks only to this interface, so the file store can be swapped from
local disk to OneDrive/SharePoint (via Microsoft Graph) by changing one config
value — STORAGE_PROVIDER — with no other code changes.

Folder model (two levels):   <Employee Name> / <Month-Year> / <files>
"""
from __future__ import annotations

from abc import ABC, abstractmethod
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
    rel_path: str        # "<emp>/February-2026"
    file_count: int


@dataclass
class EmployeeFolder:
    name: str            # employee name
    rel_path: str
    month_count: int


class StorageProvider(ABC):
    # ---- listing ----
    @abstractmethod
    def list_employees(self) -> list[EmployeeFolder]: ...

    @abstractmethod
    def list_months(self, employee: str) -> list[MonthFolder]: ...

    @abstractmethod
    def list_items(self, employee: str, month: str) -> list[FileItem]: ...

    # ---- reading ----
    @abstractmethod
    def read_file(self, rel_path: str) -> tuple[bytes, str, str]:
        """Return (bytes, filename, content_type)."""

    # ---- writing (used by the ingestion pipeline) ----
    @abstractmethod
    def save_file(self, employee: str, month_label: str, filename: str, data: bytes) -> str: ...

    @abstractmethod
    def save_text(self, employee: str, month_label: str, filename: str, text: str) -> str: ...

    # ---- folder CRUD (used by the Files page) ----
    @abstractmethod
    def create_employee(self, name: str) -> EmployeeFolder: ...

    @abstractmethod
    def create_month(self, employee: str, month_label: str) -> MonthFolder: ...

    @abstractmethod
    def rename_folder(self, rel_path: str, new_name: str) -> str: ...

    @abstractmethod
    def delete_folder(self, rel_path: str) -> None: ...

    @abstractmethod
    def delete_file(self, rel_path: str) -> None: ...
