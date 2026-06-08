"""
OneDrive / SharePoint storage provider — STUB (3-level: Manager / Employee / Month-Year).

Wire this once your Microsoft Graph app registration is ready. The interface is
identical to LocalStorageProvider, so flipping STORAGE_PROVIDER=onedrive is the
only change the rest of the app needs.
"""
from __future__ import annotations

from app.services.storage_provider.base import StorageProvider


class OneDriveStorageProvider(StorageProvider):
    def __init__(self) -> None:
        raise NotImplementedError(
            "OneDriveStorageProvider is a stub. Keep STORAGE_PROVIDER=local until "
            "the Graph app registration + drive id are configured."
        )

    def list_managers(self): raise NotImplementedError
    def list_employees(self, manager): raise NotImplementedError
    def list_months(self, manager, employee): raise NotImplementedError
    def list_items(self, manager, employee, month): raise NotImplementedError
    def read_file(self, rel_path): raise NotImplementedError
    def save_file(self, manager, employee, month_label, filename, data): raise NotImplementedError
    def save_text(self, manager, employee, month_label, filename, text): raise NotImplementedError
    def create_manager(self, name): raise NotImplementedError
    def create_employee(self, manager, name): raise NotImplementedError
    def create_month(self, manager, employee, month_label): raise NotImplementedError
    def rename_folder(self, rel_path, new_name): raise NotImplementedError
    def delete_folder(self, rel_path): raise NotImplementedError
    def delete_file(self, rel_path): raise NotImplementedError
