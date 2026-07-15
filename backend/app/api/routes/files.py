"""
Files routes — browse and manage the <Manager>/<Employee>/<Month-Year>/<files> tree.

Backed by the active storage provider (local now, OneDrive later), so the same
CRUD the UI performs here will create/rename/delete folders in OneDrive once
STORAGE_PROVIDER=onedrive.
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.http_headers import content_disposition
from app.services import storage_provider as sp
from app.services.storage_provider.archive import iter_zip, scope_size, year_summary

router = APIRouter(prefix="/files", tags=["files"])


def _safe_filename(name: str) -> str:
    """Strip path separators / unsafe chars so a download name or an uploaded
    filename can't escape its folder or break the Content-Disposition header."""
    import re
    base = (name or "").replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base)
    return base or "file"


class CreateManager(BaseModel):
    name: str


class CreateEmployee(BaseModel):
    name: str


class CreateMonth(BaseModel):
    month_label: str


class RenameFolder(BaseModel):
    rel_path: str
    new_name: str


# ---- 3-level listing ----

@router.get("/managers")
def list_managers():
    return [m.__dict__ for m in sp.get_storage_provider().list_managers()]


@router.get("/managers/{manager}/employees")
def list_employees(manager: str):
    return [e.__dict__ for e in sp.get_storage_provider().list_employees(manager)]


@router.get("/managers/{manager}/employees/{employee}/months")
def list_months(manager: str, employee: str):
    return [m.__dict__ for m in sp.get_storage_provider().list_months(manager, employee)]


@router.get("/managers/{manager}/employees/{employee}/months/{month}/items")
def list_items(manager: str, employee: str, month: str):
    return [i.__dict__ for i in sp.get_storage_provider().list_items(manager, employee, month)]


# ---- reading ----

@router.get("/content")
def file_content(rel_path: str = Query(...)):
    try:
        data, name, ctype = sp.get_storage_provider().read_file(rel_path)
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    disp = "inline" if ctype.startswith(("image/", "application/pdf", "text/", "application/json", "message/")) else "attachment"
    return Response(content=data, media_type=ctype,
                    headers={"Content-Disposition": content_disposition(disp, name)})


@router.get("/eml-preview")
def eml_preview(rel_path: str = Query(...)):
    """Parse an EML file and return its structured content as JSON."""
    try:
        data, name, _ctype = sp.get_storage_provider().read_file(rel_path)
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    if not name.lower().endswith(".eml"):
        raise HTTPException(400, "Not an EML file")
    from app.services.extraction.eml_parser import parse_eml
    return parse_eml(data)


# ---- ZIP download ----

@router.get("/years")
def vault_years():
    """Years present in the vault (newest first) with file count + total bytes —
    populates the year-wise download dropdown. Metadata only (no downloads)."""
    return year_summary()


@router.get("/download-size")
def download_size(
    manager: str | None = Query(default=None),
    rel_path: str | None = Query(default=None),
    year: int | None = Query(default=None),
):
    """Total {files, bytes} of a download scope, so the UI can show an accurate
    progress bar. Cheap: a single metadata listing, no file downloads."""
    return scope_size(manager=manager, rel_prefix=(rel_path or None), year=year)


@router.get("/download-zip")
def download_zip(
    manager: str | None = Query(default=None),
    rel_path: str | None = Query(default=None, description="Scope to a subtree, "
                                 "e.g. '<Manager>/<Employee>' or '.../<Month-Year>'"),
    year: int | None = Query(default=None, description="Scope to one calendar year"),
):
    """
    STREAM the storage tree as a ZIP (never buffered fully in memory, so it
    scales to multi-GB vaults and starts downloading immediately).
      ?year=2026            → everything filed under 2026 (bounded ≈ ≤5 GB)
      ?manager=X            → that account-manager's subtree (fast S3 prefix scan)
      ?rel_path=A/B[/C]     → just one employee or month folder
      (no query)            → the entire archive
    Filters combine (e.g. ?manager=X&year=2026).
    """
    scope = (rel_path or "").strip("/")
    name_bits = []
    if scope:
        name_bits.append(_safe_filename(scope.split("/")[-1]))
    if manager and not scope:
        name_bits.append(_safe_filename(manager))
    if year:
        name_bits.append(str(year))
    filename = ("_".join(name_bits) or "timesheets_archive") + ".zip"
    stream = iter_zip(manager=manager, rel_prefix=(scope or None), year=year)
    return StreamingResponse(
        stream,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition("attachment", filename)},
    )


# ---- folder CRUD ----

@router.post("/managers", status_code=201)
def create_manager(body: CreateManager):
    if not body.name.strip():
        raise HTTPException(400, "Name required")
    return sp.get_storage_provider().create_manager(body.name).__dict__


@router.post("/managers/{manager}/employees", status_code=201)
def create_employee(manager: str, body: CreateEmployee):
    if not body.name.strip():
        raise HTTPException(400, "Name required")
    return sp.get_storage_provider().create_employee(manager, body.name).__dict__


@router.post("/managers/{manager}/employees/{employee}/months", status_code=201)
def create_month(manager: str, employee: str, body: CreateMonth):
    if not body.month_label.strip():
        raise HTTPException(400, "Month label required")
    return sp.get_storage_provider().create_month(manager, employee, body.month_label).__dict__


@router.patch("/folder")
def rename_folder(body: RenameFolder):
    try:
        new_rel = sp.get_storage_provider().rename_folder(body.rel_path, body.new_name)
    except FileNotFoundError:
        raise HTTPException(404, "Folder not found")
    return {"rel_path": new_rel}


@router.delete("/folder")
def delete_folder(rel_path: str = Query(...)):
    sp.get_storage_provider().delete_folder(rel_path)
    return {"deleted": rel_path}


@router.delete("/file")
def delete_file(rel_path: str = Query(...)):
    sp.get_storage_provider().delete_file(rel_path)
    return {"deleted": rel_path}


# ---- upload into an existing month folder (manual vault management) ----

@router.post("/managers/{manager}/employees/{employee}/months/{month}/files", status_code=201)
async def upload_files_to_month(
    manager: str, employee: str, month: str, files: list[UploadFile] = File(...),
):
    """Add one or more files directly into a specific employee/month vault folder.

    Lets a reviewer drop a corrected/extra PDF (or any document) into a month
    without going through extraction. Stored via the active provider, so it lands
    on local disk or S3 exactly like pipeline output."""
    if not files:
        raise HTTPException(400, "No files provided.")
    provider = sp.get_storage_provider()
    saved: list[dict] = []
    for uf in files:
        data = await uf.read()
        if not data:
            continue
        name = _safe_filename(uf.filename or "upload.bin")
        rel = provider.save_file(manager, employee, month, name, data)
        saved.append({"name": name, "rel_path": rel, "size": len(data),
                      "content_type": uf.content_type or "application/octet-stream"})
    if not saved:
        raise HTTPException(400, "All uploaded files were empty.")
    return {"saved": saved}
