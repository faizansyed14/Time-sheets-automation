"""
AWS S3 storage provider (STORAGE_PROVIDER=s3).

Maps the 3-level folder model onto S3 key prefixes:
    {S3_PREFIX}/<Manager>/<Employee>/<Month-Year>/<file>

S3 has no real directories, so an empty folder is represented by a zero-byte
`.keep` marker object. Listing uses delimiter="/" to walk one level at a time.

Config (.env): S3_BUCKET, S3_PREFIX, S3_REGION, AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY (omit the keys on EC2/ECS to use the instance IAM role),
and optionally S3_ENDPOINT_URL for MinIO / S3-compatible stores.
"""
from __future__ import annotations

import mimetypes
import re
from functools import cached_property

from app.core.config import settings
from app.services.storage_provider.base import (
    EmployeeFolder,
    FileItem,
    ManagerFolder,
    MonthFolder,
    StorageProvider,
)

_KEEP = ".keep"


def _safe(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r'[<>:"\\|?*]+', "_", name)   # keep "/" out of a single segment
    name = name.replace("/", "_")
    return name or "Unknown"


class S3StorageProvider(StorageProvider):
    def __init__(self) -> None:
        if not settings.s3_bucket:
            raise RuntimeError("STORAGE_PROVIDER=s3 requires S3_BUCKET to be set.")
        self.bucket = settings.s3_bucket
        self.prefix = (settings.s3_prefix or "").strip("/")

    @cached_property
    def _client(self):
        import boto3

        kwargs = {"region_name": settings.s3_region}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        return boto3.client("s3", **kwargs)

    # ---- key helpers ----
    def _key(self, *parts: str) -> str:
        segs = ([self.prefix] if self.prefix else []) + [p for p in parts if p]
        return "/".join(segs)

    def _folders(self, prefix: str) -> list[str]:
        """Immediate sub-'folder' names under a key prefix (via CommonPrefixes)."""
        p = prefix.rstrip("/") + "/"
        out: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=p, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                name = cp["Prefix"][len(p):].rstrip("/")
                if name:
                    out.append(name)
        return sorted(out)

    def _count_folders(self, prefix: str) -> int:
        return len(self._folders(prefix))

    # ---- listing ----
    def list_managers(self) -> list[ManagerFolder]:
        base = self._key()
        return [ManagerFolder(name=n, rel_path=n, employee_count=self._count_folders(self._key(n)))
                for n in self._folders(base) if not n.startswith(("_", "."))]

    def list_employees(self, manager: str) -> list[EmployeeFolder]:
        m = _safe(manager)
        return [EmployeeFolder(name=n, rel_path=f"{m}/{n}",
                               month_count=self._count_folders(self._key(m, n)))
                for n in self._folders(self._key(m))]

    def list_months(self, manager: str, employee: str) -> list[MonthFolder]:
        m, e = _safe(manager), _safe(employee)
        out: list[MonthFolder] = []
        for n in self._folders(self._key(m, e)):
            files = self.list_items(manager, employee, n)
            out.append(MonthFolder(name=n, rel_path=f"{m}/{e}/{n}", file_count=len(files)))
        return out

    def list_items(self, manager: str, employee: str, month: str) -> list[FileItem]:
        m, e, mo = _safe(manager), _safe(employee), _safe(month)
        p = self._key(m, e, mo).rstrip("/") + "/"
        out: list[FileItem] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=p, Delimiter="/"):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(p):]
                if not name or name == _KEEP:
                    continue
                out.append(FileItem(
                    name=name, rel_path=f"{m}/{e}/{mo}/{name}", size=obj["Size"],
                    content_type=mimetypes.guess_type(name)[0] or "application/octet-stream"))
        return sorted(out, key=lambda f: f.name)

    # ---- reading ----
    def read_file(self, rel_path: str) -> tuple[bytes, str, str]:
        key = self._key(*[_safe(p) for p in rel_path.split("/")[:-1]] + [rel_path.split("/")[-1]])
        try:
            obj = self._client.get_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            raise FileNotFoundError(rel_path) from e
        name = rel_path.split("/")[-1]
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return obj["Body"].read(), name, ctype

    # ---- writing ----
    def _put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

    def save_file(self, manager: str, employee: str, month_label: str, filename: str, data: bytes) -> str:
        m, e, mo, f = _safe(manager), _safe(employee), _safe(month_label), _safe(filename)
        self._put(self._key(m, e, mo, f), data, mimetypes.guess_type(f)[0])
        return f"{m}/{e}/{mo}/{f}"

    def save_text(self, manager: str, employee: str, month_label: str, filename: str, text: str) -> str:
        return self.save_file(manager, employee, month_label, filename, text.encode("utf-8"))

    # ---- folder CRUD (markers) ----
    def create_manager(self, name: str) -> ManagerFolder:
        m = _safe(name)
        self._put(self._key(m, _KEEP), b"")
        return ManagerFolder(name=m, rel_path=m, employee_count=0)

    def create_employee(self, manager: str, name: str) -> EmployeeFolder:
        m, e = _safe(manager), _safe(name)
        self._put(self._key(m, e, _KEEP), b"")
        return EmployeeFolder(name=e, rel_path=f"{m}/{e}", month_count=0)

    def create_month(self, manager: str, employee: str, month_label: str) -> MonthFolder:
        m, e, mo = _safe(manager), _safe(employee), _safe(month_label)
        self._put(self._key(m, e, mo, _KEEP), b"")
        return MonthFolder(name=mo, rel_path=f"{m}/{e}/{mo}", file_count=0)

    def rename_folder(self, rel_path: str, new_name: str) -> str:
        parts = [p for p in rel_path.split("/") if p]
        old_prefix = self._key(*[_safe(p) for p in parts]).rstrip("/") + "/"
        new_parts = parts[:-1] + [_safe(new_name)]
        new_prefix = self._key(*[_safe(p) for p in new_parts]).rstrip("/") + "/"
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=old_prefix):
            for obj in page.get("Contents", []):
                src = obj["Key"]
                dst = new_prefix + src[len(old_prefix):]
                self._client.copy_object(Bucket=self.bucket,
                                         CopySource={"Bucket": self.bucket, "Key": src}, Key=dst)
                self._client.delete_object(Bucket=self.bucket, Key=src)
        return "/".join(new_parts)

    def delete_folder(self, rel_path: str) -> None:
        prefix = self._key(*[_safe(p) for p in rel_path.split("/") if p]).rstrip("/") + "/"
        self._delete_prefix(prefix)

    def delete_file(self, rel_path: str) -> None:
        parts = rel_path.split("/")
        key = self._key(*[_safe(p) for p in parts[:-1]] + [parts[-1]])
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def _delete_prefix(self, prefix: str) -> None:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
