"""
OneDrive / SharePoint storage provider — STUB.

Wire this once your Microsoft Graph app registration is ready. The interface is
identical to LocalStorageProvider, so flipping STORAGE_PROVIDER=onedrive is the
only change the rest of the app needs — every folder CRUD action the UI performs
will then create/rename/delete real folders in the shared drive.

Implementation outline (same app-only token as the mail provider):
  base: https://graph.microsoft.com/v1.0/drives/{drive_id}/root
  list children : GET .../root:/<path>:/children
  create folder : POST .../root:/<parent>:/children  { name, folder: {} }
  rename        : PATCH .../items/{id}                { name }
  delete        : DELETE .../items/{id}
  upload file   : PUT .../root:/<path>/<name>:/content
  download      : GET .../root:/<path>:/content
Map Graph item ids <-> your <Employee>/<Month-Year> rel paths.
"""
from __future__ import annotations

from app.services.storage_provider.base import StorageProvider


class OneDriveStorageProvider(StorageProvider):
    def __init__(self) -> None:
        raise NotImplementedError(
            "OneDriveStorageProvider is a stub. Keep STORAGE_PROVIDER=local until "
            "the Graph app registration + drive id are configured."
        )

    def list_employees(self): raise NotImplementedError
    def list_months(self, employee): raise NotImplementedError
    def list_items(self, employee, month): raise NotImplementedError
    def read_file(self, rel_path): raise NotImplementedError
    def save_file(self, employee, month_label, filename, data): raise NotImplementedError
    def save_text(self, employee, month_label, filename, text): raise NotImplementedError
    def create_employee(self, name): raise NotImplementedError
    def create_month(self, employee, month_label): raise NotImplementedError
    def rename_folder(self, rel_path, new_name): raise NotImplementedError
    def delete_folder(self, rel_path): raise NotImplementedError
    def delete_file(self, rel_path): raise NotImplementedError
