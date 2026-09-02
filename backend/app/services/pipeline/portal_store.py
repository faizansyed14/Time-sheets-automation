"""
Storage for the employee portal's submission uploads — the employee's ORIGINAL
files (timesheet / sick-leave certificate / other), kept for the submission's
life.

This is a SEPARATE store from raw_store.py: raw_store's copies exist only so a
failed/needs-review file can be retried, and are deleted once processed —
portal uploads need to persist for as long as the submission does, so
repurposing raw_store's lifecycle would be wrong. Same local/S3 branching
pattern (settings.storage_provider), under its own root/prefix so neither
store's files collide with or get purged by the other's cleanup logic.
"""
from __future__ import annotations

import re
import shutil
from functools import lru_cache

from app.core.config import settings


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name or "file") or "file"


def _contained(root, candidate):
    """Real path containment, not a string-prefix check — same technique as
    storage_provider/local_provider.py's LocalStorageProvider._abs. Every
    caller here passes rel_path from a DB row (PortalSubmissionFile.
    stored_path), never straight from a request param, so this is defense in
    depth rather than closing a directly-reachable hole — cheap enough to
    apply anyway."""
    root = root.resolve()
    try:
        p = candidate.resolve()
    except Exception:
        return None
    if p != root and not p.is_relative_to(root):
        return None
    return p


def _use_s3() -> bool:
    return (settings.storage_provider or "local").lower() == "s3" and bool(settings.s3_bucket)


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
    vault = (settings.s3_prefix or "").strip("/")
    portal = (settings.s3_portal_prefix or "_portal-uploads").strip("/")
    segs = [s for s in (vault, portal) if s] + [rel.lstrip("/")]
    return "/".join(segs)


def save_portal_file(submission_id: str, kind: str, filename: str, data: bytes) -> str:
    """Persist the employee's original upload; return a relative key
    '<submission_id>/<kind>__<filename>' — raises on failure (unlike
    raw_store.save_raw's best-effort None, since a portal upload with nowhere
    to go must fail the request loudly, not silently lose the file)."""
    safe = f"{kind}__{_safe(filename)}"
    rel = f"{submission_id}/{safe}"
    if _use_s3():
        _s3_client().put_object(Bucket=settings.s3_bucket, Key=_s3_key(rel), Body=data)
    else:
        folder = settings.portal_uploads_path / submission_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / safe).write_bytes(data)
    return rel


def read_portal_file(rel_path: str | None) -> bytes | None:
    if not rel_path:
        return None
    if _use_s3():
        try:
            obj = _s3_client().get_object(Bucket=settings.s3_bucket, Key=_s3_key(rel_path))
            return obj["Body"].read()
        except Exception:
            return None
    p = _contained(settings.portal_uploads_path, settings.portal_uploads_path / rel_path)
    return p.read_bytes() if p is not None and p.is_file() else None


def delete_portal_file(rel_path: str | None) -> None:
    """Remove one stored file (best-effort) — used when a slot is replaced or
    deleted before submit. Does not touch sibling slots in the same submission."""
    if not rel_path:
        return
    if _use_s3():
        try:
            _s3_client().delete_object(Bucket=settings.s3_bucket, Key=_s3_key(rel_path))
        except Exception:
            pass
        return
    try:
        p = (settings.portal_uploads_path / rel_path).resolve()
        if p.is_file():
            p.unlink()
    except Exception:
        pass


def render_response(filename: str, data: bytes, page: int):
    """Server-side DOCX/XLSX/PDF page-image render — same mechanism as
    pipeline.py's /raw-render, reused here so a portal file previews with
    full format support (not just PDF/images) in every viewer: employee,
    manager, and the internal Pipeline page all hit this."""
    from fastapi import HTTPException, Response

    from app.services.extraction.file_processor import detect_file_type, to_page_images

    ftype = detect_file_type(filename, data)
    if ftype not in ("docx", "xlsx", "pdf"):
        raise HTTPException(400, f"No server render for type '{ftype}'")
    try:
        imgs = to_page_images(ftype, data)
    except Exception:
        raise HTTPException(422, "Could not render this file")
    if not imgs:
        raise HTTPException(422, "Could not render this file")
    idx = min(page, len(imgs)) - 1
    return Response(content=imgs[idx], media_type="image/jpeg",
                    headers={"X-Page-Count": str(len(imgs))})


def content_response(filename: str, content_type: str | None, data: bytes):
    """A Response for inline preview of one submission file — same
    inline-vs-attachment logic as pipeline.py's raw-preview route, reused
    here so a portal file previews identically whether an employee, a
    manager, or the internal team is the one looking at it."""
    from fastapi import Response

    from app.core.http_headers import content_disposition
    from app.services.extraction.file_processor import content_type_for, detect_file_type

    media = content_type_for(filename, data, content_type)
    ftype = detect_file_type(filename, data)
    inline = ftype in ("pdf", "image") or media.startswith(("image/", "application/pdf"))
    disp = "inline" if inline else "attachment"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": content_disposition(disp, filename)})


def delete_submission_files(submission_id: str) -> None:
    """Remove every stored file for a submission (best-effort)."""
    if _use_s3():
        try:
            client = _s3_client()
            prefix = _s3_key(f"{submission_id}/")
            resp = client.list_objects_v2(Bucket=settings.s3_bucket, Prefix=prefix)
            keys = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=settings.s3_bucket, Delete={"Objects": keys})
        except Exception:
            pass
        return
    folder = settings.portal_uploads_path / submission_id
    try:
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        pass
