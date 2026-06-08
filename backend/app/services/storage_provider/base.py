"""
Storage provider abstraction.

The app talks only to this interface, so the file store can be swapped from
local disk to OneDrive/SharePoint (via Microsoft Graph) by changing one config
value — STORAGE_PROVIDER — with no other code changes.

Folder model (THREE levels):   <Account Manager> / <Employee Name> / <Month-Year> / <files>
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
