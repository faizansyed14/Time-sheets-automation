"""
Files routes — browse and manage the <Employee>/<Month-Year>/<files> tree.

Backed by the active storage provider (local now, OneDrive later), so the same
CRUD the UI performs here will create/rename/delete folders in OneDrive once
STORAGE_PROVIDER=onedrive.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query, Response
from pydantic import BaseModel

from app.services import storage_provider as sp

router = APIRouter(prefix="/files", tags=["files"])


class CreateEmployee(BaseModel):
    name: str


class CreateMonth(BaseModel):
    month_label: str


class RenameFolder(BaseModel):
    rel_path: str
    new_name: str


@router.get("/employees")
def list_employees():
    return [e.__dict__ for e in sp.get_storage_provider().list_employees()]


@router.get("/employees/{employee}/months")
def list_months(employee: str):
    return [m.__dict__ for m in sp.get_storage_provider().list_months(employee)]


@router.get("/employees/{employee}/months/{month}/items")
def list_items(employee: str, month: str):
    return [i.__dict__ for i in sp.get_storage_provider().list_items(employee, month)]


@router.get("/content")
def file_content(rel_path: str = Query(...)):
    try:
        data, name, ctype = sp.get_storage_provider().read_file(rel_path)
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    disp = "inline" if ctype.startswith(("image/", "application/pdf", "text/", "application/json")) else "attachment"
    return Response(content=data, media_type=ctype,
                    headers={"Content-Disposition": f'{disp}; filename="{name}"'})


@router.post("/employees", status_code=201)
def create_employee(body: CreateEmployee):
    if not body.name.strip():
        raise HTTPException(400, "Name required")
    return sp.get_storage_provider().create_employee(body.name).__dict__


@router.post("/employees/{employee}/months", status_code=201)
def create_month(employee: str, body: CreateMonth):
    if not body.month_label.strip():
        raise HTTPException(400, "Month label required")
    return sp.get_storage_provider().create_month(employee, body.month_label).__dict__


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
