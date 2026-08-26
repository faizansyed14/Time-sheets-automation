"""
Files routes — browse and manage the <Manager>/<Employee>/<Month-Year>/<files> tree.

Backed by the active storage provider (local now, OneDrive later), so the same
CRUD the UI performs here will create/rename/delete folders in OneDrive once
STORAGE_PROVIDER=onedrive.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.http_headers import content_disposition
from app.models.employee import Employee
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


# ---- alternate (project-wise / location-wise / search) navigation onto the
# SAME vault files ----
#
# The vault's physical layout is (and stays) Manager/Employee/Month — that's
# what save_file/create_month/etc. above actually write to. These are
# second, read-oriented lenses onto the identical files: grouped by the
# Employee Matcher's own `project` or `location` field instead of the
# manager, or found directly by a name/ID/project search. Nothing here
# creates a parallel storage structure: every route below resolves back down
# to the exact same (manager, employee_folder, month) triple the Manager
# view already uses, so every file-level action (preview, delete, upload,
# zip download) works via the SAME existing endpoints/rel_paths — a row
# found this way IS the Manager-view file, just reached by a different path.
# Only the folder-CRUD routes above (create/rename/delete a folder) have no
# equivalent here — a project/location's employee roster comes from the
# Employee Matcher, not from folders created by hand.

async def _distinct_grouping(db: AsyncSession, column) -> list[dict]:
    rows = (await db.execute(
        select(column, func.count(Employee.id))
        .where(column.is_not(None), column != "")
        .group_by(column)
        .order_by(column)
    )).all()
    return [{"name": name, "employee_count": count} for name, count in rows]


@router.get("/projects")
async def list_projects(db: AsyncSession = Depends(get_db)):
    return await _distinct_grouping(db, Employee.project)


@router.get("/locations")
async def list_locations(db: AsyncSession = Depends(get_db)):
    return await _distinct_grouping(db, Employee.location)


_VAULT_LOOKUP_CONCURRENCY = 20


async def _employees_with_vault_info(rows: list[Employee]) -> list[dict]:
    """One row per employee, each needing its own vault lookup for
    month_count — a group's employees (by project, location, or a search
    match) can be scattered across many different manager folders, so
    there's no single listing that covers them all at once. Run those
    lookups CONCURRENTLY, bounded to _VAULT_LOOKUP_CONCURRENCY at a time,
    and off the event loop (asyncio.to_thread) — sequential + on-loop was
    fine for local disk (a directory stat is microseconds) but would
    head-of-line block on S3, where each lookup is a real network round
    trip: a large roster could turn a sub-second request into one taking
    tens of seconds, and every other request sharing this worker would
    stall behind it meanwhile."""
    import asyncio

    sem = asyncio.Semaphore(_VAULT_LOOKUP_CONCURRENCY)
    provider = sp.get_storage_provider()

    async def _month_count(manager: str, folder: str) -> int:
        async with sem:
            try:
                return await asyncio.to_thread(lambda: len(provider.list_months(manager, folder)))
            except Exception:
                return 0

    locations = [sp.employee_vault_location(e.account_manager, e.name, e.aco_number, e.dco_number)
                 for e in rows]
    counts = await asyncio.gather(*(_month_count(m, f) for m, f in locations))

    return [
        {
            "employee_pk": e.id, "employee_id": e.employee_id, "name": e.name,
            "project": e.project, "location": e.location,
            "account_manager": manager, "employee_folder": folder, "month_count": count,
        }
        for e, (manager, folder), count in zip(rows, locations, counts)
    ]


@router.get("/projects/{project}/employees")
async def list_project_employees(project: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Employee).where(Employee.project == project).order_by(Employee.name)
    )).scalars().all()
    return await _employees_with_vault_info(rows)


@router.get("/locations/{location}/employees")
async def list_location_employees(location: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Employee).where(Employee.location == location).order_by(Employee.name)
    )).scalars().all()
    return await _employees_with_vault_info(rows)


# Hard cap on a search's fan-out — _employees_with_vault_info's own
# concurrency limit keeps any single batch from monopolising the worker, but
# a very broad query (e.g. one common letter) could still match hundreds of
# employees; capping the SQL result itself is what actually bounds the work,
# not just how fast that work runs.
_SEARCH_RESULT_LIMIT = 25


@router.get("/search-employees")
async def search_vault_employees(q: str = Query(..., min_length=1), db: AsyncSession = Depends(get_db)):
    """Employee name, employee ID, or project ("client name") — one search
    across the whole Employee Matcher, used by each view's search bar to
    jump straight into an employee's vault without manually drilling down
    through Manager/Project/Location first."""
    like = f"%{q.strip()}%"
    rows = (await db.execute(
        select(Employee)
        .where(
            Employee.name.ilike(like)
            | Employee.employee_id.ilike(like)
            | Employee.project.ilike(like)
        )
        .order_by(Employee.name)
        .limit(_SEARCH_RESULT_LIMIT)
    )).scalars().all()
    return await _employees_with_vault_info(rows)


async def _resolve_employee_vault(db: AsyncSession, employee_pk: str) -> tuple[str, str]:
    e = (await db.execute(select(Employee).where(Employee.id == employee_pk))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    return sp.employee_vault_location(e.account_manager, e.name, e.aco_number, e.dco_number)


@router.get("/employee-vault/{employee_pk}/months")
async def list_employee_vault_months(employee_pk: str, db: AsyncSession = Depends(get_db)):
    manager, folder = await _resolve_employee_vault(db, employee_pk)
    return [m.__dict__ for m in sp.get_storage_provider().list_months(manager, folder)]


@router.get("/employee-vault/{employee_pk}/months/{month}/items")
async def list_employee_vault_items(employee_pk: str, month: str, db: AsyncSession = Depends(get_db)):
    manager, folder = await _resolve_employee_vault(db, employee_pk)
    return [i.__dict__ for i in sp.get_storage_provider().list_items(manager, folder, month)]


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
    """Parse an EML (or Outlook .msg) file and return its structured content
    as JSON."""
    try:
        data, name, _ctype = sp.get_storage_provider().read_file(rel_path)
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    if not name.lower().endswith((".eml", ".msg")):
        raise HTTPException(400, "Not an EML or .msg file")
    from app.services.extraction.eml_parser import parse_eml
    try:
        return parse_eml(data, filename=name)
    except Exception:
        raise HTTPException(422, "Could not parse this email file")


@router.post("/eml-preview-upload")
async def eml_preview_upload(file: UploadFile = File(...)):
    """Parse raw EML (or Outlook .msg) bytes → structured content.

    The GET form needs a stored file. An email attached INSIDE another email
    only exists as bytes in the parent's preview payload, so nesting could not
    be opened at all. Posting the bytes back makes a forwarded email — or a
    .msg attached inside one — open like any other, at any depth.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    from app.services.extraction.eml_parser import parse_eml
    try:
        return parse_eml(data, filename=file.filename)
    except Exception:
        raise HTTPException(422, "Could not parse this email file")


@router.get("/render")
def render_file(
    rel_path: str = Query(...),
    page: int = Query(default=1, ge=1, le=50),
):
    """Server-render a stored PDF/DOCX/XLSX file to page images so vault preview
    works in every browser, same as inbox/pipeline previews."""
    try:
        data, name, _ctype = sp.get_storage_provider().read_file(rel_path)
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    from app.services.extraction.file_processor import detect_file_type, to_page_images
    ftype = detect_file_type(name or "", data)
    if ftype not in ("docx", "xlsx", "pdf"):
        raise HTTPException(400, f"No server render for type '{ftype}'")
    try:
        imgs = to_page_images(ftype, data)
    except Exception:
        raise HTTPException(422, "Could not render this file")
    if not imgs:
        raise HTTPException(422, "Could not render this file")
    idx = min(page, len(imgs)) - 1
    return Response(
        content=imgs[idx],
        media_type="image/jpeg",
        headers={"X-Page-Count": str(len(imgs))},
    )


@router.post("/render-upload")
async def render_uploaded_file(
    file: UploadFile = File(...),
    page: int = Query(default=1, ge=1, le=50),
):
    """Server-render raw uploaded PDF/DOCX/XLSX bytes to page images.

    Used by the EML previewer when an attachment is embedded inside the .eml
    preview JSON (so there is no existing URL/attachment-id render route)."""
    data = await file.read()
    name = file.filename or "file"
    from app.services.extraction.file_processor import detect_file_type, to_page_images
    ftype = detect_file_type(name, data)
    if ftype not in ("docx", "xlsx", "pdf"):
        raise HTTPException(400, f"No server render for type '{ftype}'")
    try:
        imgs = to_page_images(ftype, data)
    except Exception:
        raise HTTPException(422, "Could not render this file")
    if not imgs:
        raise HTTPException(422, "Could not render this file")
    idx = min(page, len(imgs)) - 1
    return Response(
        content=imgs[idx],
        media_type="image/jpeg",
        headers={"X-Page-Count": str(len(imgs))},
    )


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
    employee: list[str] = Query(default=[]),
    month: str | None = Query(default=None),
):
    """Total {files, bytes} of a download scope, so the UI can show an accurate
    progress bar. Cheap: a single metadata listing, no file downloads."""
    return scope_size(manager=manager, rel_prefix=(rel_path or None), year=year,
                       employees=employee or None, month=month)


@router.get("/download-zip")
def download_zip(
    manager: str | None = Query(default=None),
    rel_path: str | None = Query(default=None, description="Scope to a subtree, "
                                 "e.g. '<Manager>/<Employee>' or '.../<Month-Year>'"),
    year: int | None = Query(default=None, description="Scope to one calendar year"),
    employee: list[str] = Query(
        default=[], description="Repeat to pick specific employees under `manager` "
                                "(1 or a few, instead of the whole team)"),
    month: str | None = Query(default=None, description="A bare month name, e.g. 'March'"),
):
    """
    STREAM the storage tree as a ZIP (never buffered fully in memory, so it
    scales to multi-GB vaults and starts downloading immediately).
      ?year=2026                    → everything filed under 2026 (bounded ≈ ≤5 GB)
      ?manager=X                    → that account-manager's subtree (fast S3 prefix scan)
      ?manager=X&employee=A&employee=B → just those employees under X
      ?month=March                  → that month, every year (or one year with &year=)
      ?rel_path=A/B[/C]             → just one employee or month folder
      (no query)                    → the entire archive
    Filters combine (e.g. ?manager=X&year=2026&employee=A).
    """
    scope = (rel_path or "").strip("/")
    name_bits = []
    if scope:
        name_bits.append(_safe_filename(scope.split("/")[-1]))
    if manager and not scope:
        name_bits.append(_safe_filename(manager))
    if employee:
        name_bits.append(
            "_".join(_safe_filename(e) for e in employee[:3])
            + (f"_+{len(employee) - 3}more" if len(employee) > 3 else ""))
    if month:
        name_bits.append(_safe_filename(month))
    if year:
        name_bits.append(str(year))
    filename = ("_".join(name_bits) or "timesheets_archive") + ".zip"
    stream = iter_zip(manager=manager, rel_prefix=(scope or None), year=year,
                       employees=employee or None, month=month)
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
