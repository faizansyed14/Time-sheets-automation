"""
Storage for the pipeline's *raw retry copies* — a byte-for-byte original of every
ingested file, kept ONLY so a failed / needs-review file can be re-processed.

Where the copy lives:
  - STORAGE_PROVIDER=s3  -> S3, under a dedicated prefix (settings.s3_raw_prefix,
    default "pipeline-raw") that is SEPARATE from the File Vault prefix, so these
    originals never show up in the browsable tree. Nothing touches local disk.
  - otherwise (local/onedrive) -> local disk under settings.pipeline_raw_path
    (data/pipeline_raw/<id>/<file>).

Lifecycle: a copy is created on ingest, READ on retry / manual-fix, and
DELETED once the file is processed successfully or its pipeline entry is removed.
So the store only ever holds originals for files that are still awaiting a retry
(failed / needs-review) — it does not grow without bound.
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from app.core.config import settings


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name or "file") or "file"


def _use_s3() -> bool:
    return (settings.storage_provider or "local").lower() == "s3" and bool(settings.s3_bucket)


# --------------------------------------------------------------------------- S3
@lru_cache(maxsize=1)
def _s3_client():
    import boto3

    kwargs = {"region_name": settings.s3_region}
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    return boto3.client("s3", **kwargs)


def _s3_key(rel: str) -> str:
    # Nest under the vault prefix: <s3_prefix>/<s3_raw_prefix>/<rel>, e.g.
    # timesheets/_pipeline-raw/<id>/<file>. Visible in the S3 console inside
    # timesheets/, hidden from the File Vault (name starts with "_"), and covered
    # by an IAM policy scoped to timesheets/*.
    vault = (settings.s3_prefix or "").strip("/")
    raw = (settings.s3_raw_prefix or "_pipeline-raw").strip("/")
    segs = [s for s in (vault, raw) if s] + [rel.lstrip("/")]
    return "/".join(segs)


# --------------------------------------------------------------------- public API
def save_raw(pipeline_id: str, filename: str, data: bytes) -> str | None:
    """Persist the original bytes; return a relative key '<id>/<file>' or None."""
    safe = _safe(filename)
    rel = f"{pipeline_id}/{safe}"
    try:
        if _use_s3():
            _s3_client().put_object(Bucket=settings.s3_bucket, Key=_s3_key(rel), Body=data)
        else:
            folder = settings.pipeline_raw_path / pipeline_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / safe).write_bytes(data)
        return rel
    except Exception:
        return None


def read_raw(rel_path: str | None) -> bytes | None:
    """Read a previously stored raw copy. Falls back to legacy local layouts."""
    if not rel_path:
        return None
    if _use_s3():
        try:
            obj = _s3_client().get_object(Bucket=settings.s3_bucket, Key=_s3_key(rel_path))
            return obj["Body"].read()
        except Exception:
            return None
    # local — try the current path, plus the older "_pipeline/" layouts.
    candidates = []
    if rel_path.startswith("_pipeline/"):
        candidates.append(settings.storage_path / rel_path)
        candidates.append(settings.pipeline_raw_path / rel_path[len("_pipeline/"):])
    else:
        candidates.append(settings.pipeline_raw_path / rel_path)
        candidates.append(settings.storage_path / "_pipeline" / rel_path)
    for p in candidates:
        try:
            p = p.resolve()
            if p.is_file():
                return p.read_bytes()
        except Exception:
            continue
    return None


def delete_raw(rel_path: str | None) -> None:
    """Remove a raw copy (best-effort). Called on success / resolve / delete."""
    if not rel_path:
        return
    if _use_s3():
        try:
            _s3_client().delete_object(Bucket=settings.s3_bucket, Key=_s3_key(rel_path))
        except Exception:
            pass
        return
    # local — drop the whole <id> folder (it only ever holds this one file).
    parts = [p for p in rel_path.split("/") if p]
    pid = parts[1] if parts and parts[0] == "_pipeline" and len(parts) > 1 else (parts[0] if parts else "")
    if not pid:
        return
    for base in (settings.pipeline_raw_path, settings.storage_path / "_pipeline"):
        folder = base / pid
        try:
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
        except Exception:
            pass


# ------------------------------------------------------------- retention purge
def purge_old(max_age_days: int | None = None) -> int:
    """Delete retry copies older than `max_age_days` (default
    settings.pipeline_raw_retention_days). Returns how many originals were
    removed. Best-effort and idempotent — safe to run on a schedule.

    A copy is normally deleted the instant its file succeeds; this is the safety
    net that bounds the store for files that stay failed/needs-review forever."""
    days = settings.pipeline_raw_retention_days if max_age_days is None else max_age_days
    if not days or days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return _purge_old_s3(cutoff) if _use_s3() else _purge_old_local(cutoff)


def _purge_old_local(cutoff: datetime) -> int:
    removed = 0
    # Current layout (data/pipeline_raw/<id>/) plus the legacy storage/_pipeline/.
    for root in (settings.pipeline_raw_path, settings.storage_path / "_pipeline"):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                mtime = datetime.fromtimestamp(child.stat().st_mtime, tz=timezone.utc)
            except Exception:
                continue
            if mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
    return removed


def _purge_old_s3(cutoff: datetime) -> int:
    removed = 0
    prefix = _s3_key("").rstrip("/") + "/"
    client = _s3_client()
    try:
        paginator = client.get_paginator("list_objects_v2")
        batch: list[dict] = []
        for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                lm = obj.get("LastModified")
                if lm is None:
                    continue
                if lm.tzinfo is None:
                    lm = lm.replace(tzinfo=timezone.utc)
                if lm < cutoff:
                    batch.append({"Key": obj["Key"]})
                    if len(batch) == 1000:  # delete_objects caps at 1000 keys
                        client.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": batch})
                        removed += len(batch)
                        batch = []
        if batch:
            client.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": batch})
            removed += len(batch)
    except Exception:
        pass
    return removed
