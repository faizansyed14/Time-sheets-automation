"""CRUD for all_employee_data (the Employee Matcher list), exposed to the UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.database import get_db
from app.models.employee import Employee
from app.services.employee.activity import set_active, left_date_of, set_left_date, stamp_joined, ym_of
from app.schemas import (
    EmployeeIn,
    EmployeeOut,
    EmployeeStatusIn,
    ImportPlan,
    ImportSummary,
)

router = APIRouter(prefix="/employee-matcher", tags=["employee-matcher"])


def _out(e: Employee) -> EmployeeOut:
    return EmployeeOut(
        id=e.id,
        employee_id=e.employee_id,
        name=e.name,
        aco_number=e.aco_number,
        dco_number=e.dco_number,
        account_manager=e.account_manager,
        employee_email_id=e.employee_email_id,
        project=e.project,
        contact_no=e.contact_no,
        location=e.location,
        work_email=e.work_email,
        personal_email=e.personal_email,
        active=e.active,
        inactive_ym=e.inactive_ym,
        joining_date=e.joining_date,
        left_date=left_date_of(e),
    )


@router.get("", response_model=list[EmployeeOut])
async def list_employees(db: AsyncSession = Depends(get_db)):
    async def _compute() -> list[dict]:
        rows = (await db.execute(select(Employee).order_by(Employee.name))).scalars().all()
        return [_out(e).model_dump() for e in rows]

    # Cached — the matcher list is read on the Employees page and Compare & Fix.
    # Returns BOTH active and inactive rows; callers filter for display.
    return await datacache.get_or_set(
        datacache.NS_EMPLOYEES, "list", datacache.TTL_EMPLOYEES, _compute)


def _same_identity(a_name: str, b_name: str) -> bool:
    return a_name.strip().lower() == b_name.strip().lower()


def _apply_derived_emails(e: Employee) -> None:
    """Recompute employee_email_id (the resolved "primary" address) from
    work_email/personal_email — the same derivation the bulk importer
    already does from its own WORK EMAIL/PERSONAL EMAIL columns (see
    import_service.py), applied here too so a manually created/edited row
    is never left with an email an editor typed into "Work email" but that
    exports/chat can't see (employee_email_id is what those still read;
    Reminders reads work_email/personal_email directly instead, so it can
    let the sender choose which one to use).

    Only overwrites when at least one of the two is actually filled in —
    an edit that doesn't touch either field (e.g. just fixing a phone
    number on a DXB-sourced row) must never wipe out an email that came
    from the original import.
    """
    if e.work_email or e.personal_email:
        e.employee_email_id = e.work_email or e.personal_email


def _check_dates(joined, left, active: bool) -> None:
    for d, label in ((joined, "Joining date"), (left, "Left date")):
        if d is not None and not (2000 <= d.year <= 2100):
            raise HTTPException(422, f"{label} must be a real date.")
    if not active and joined is not None and left is not None and left < joined:
        raise HTTPException(422, "The left date cannot be before the joining date.")


@router.post("", response_model=EmployeeOut, status_code=201)
async def create_employee(body: EmployeeIn, db: AsyncSession = Depends(get_db)):
    # The same employee_id may exist for DIFFERENT people (AUH vs DXB teams),
    # so the duplicate key is (employee_id, name) — not the ID alone.
    rows = (await db.execute(select(Employee).where(Employee.employee_id == body.employee_id))).scalars().all()
    if any(_same_identity(r.name, body.name) for r in rows):
        raise HTTPException(409, f"Employee {body.employee_id} / {body.name} already exists.")
    sent = body.model_fields_set
    data = body.model_dump(exclude={"joining_date", "left_date"})
    e = Employee(**data)
    if "joining_date" in sent:
        e.joining_date = body.joining_date          # chosen in the form (None = every month)
    elif (await db.execute(select(Employee.id).limit(1))).first() is not None:
        stamp_joined(e)        # added to a running roster: earlier months keep their headcount
    if not e.active:
        set_active(e, False)   # created already inactive -> stamp the month
        if body.left_date is not None:
            set_left_date(e, body.left_date)
    _check_dates(e.joining_date, left_date_of(e), e.active)
    _apply_derived_emails(e)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    await datacache.bust_employees()
    return _out(e)


@router.put("/{pk}", response_model=EmployeeOut)
async def update_employee(pk: str, body: EmployeeIn, db: AsyncSession = Depends(get_db)):
    e = (await db.execute(select(Employee).where(Employee.id == pk))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    others = (await db.execute(select(Employee).where(Employee.employee_id == body.employee_id))).scalars().all()
    if any(o.id != pk and _same_identity(o.name, body.name) for o in others):
        raise HTTPException(409, f"Employee {body.employee_id} / {body.name} already used by another row.")
    data = body.model_dump(exclude={"joining_date", "left_date"})
    new_active = data.pop("active")
    for k, v in data.items():
        setattr(e, k, v)
    if new_active != e.active:
        set_active(e, new_active)   # only a real change touches the inactivation month
    sent = body.model_fields_set
    if "joining_date" in sent:
        e.joining_date = body.joining_date
    if "left_date" in sent and body.left_date is not None and not e.active:
        set_left_date(e, body.left_date)        # the reviewer chose the date in the form
    _check_dates(e.joining_date, left_date_of(e), e.active)
    _apply_derived_emails(e)
    await db.commit()
    await db.refresh(e)
    await datacache.bust_employees()
    return _out(e)


@router.patch("/{pk}/status", response_model=EmployeeOut)
async def set_employee_status(pk: str, body: EmployeeStatusIn, db: AsyncSession = Depends(get_db)):
    """Activate/inactivate an employee. Inactivating is the replacement for
    delete: the row, its timesheet records and vault files are all kept —
    the employee is just excluded from active headline counts and coverage.

    Month-wise: the month they are inactivated in is recorded, and they stay in
    the Dashboard / Export for that month (and earlier ones) but not after —
    see services/employee/activity.py."""
    e = (await db.execute(select(Employee).where(Employee.id == pk))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    if body.active != e.active:
        set_active(e, body.active)
    await db.commit()
    await db.refresh(e)
    await datacache.bust_employees()
    return _out(e)


@router.post("/import/preview", response_model=ImportPlan, status_code=200)
async def preview_import_from_excel(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Dry run — exactly what this file would add/update/leave alone, plus who
    is in the matcher but NOT in the file. Writes nothing; the UI shows this
    for confirmation and only then POSTs the same file to /import."""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Only .xlsx or .xls files are accepted.")
    data = await file.read()
    from app.services.employee.import_service import build_import_plan
    try:
        return await build_import_plan(db, data)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/import", response_model=ImportSummary, status_code=200)
async def import_from_excel(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import employees from a .xlsx or legacy .xls file containing DXB and AUH sheets.

    Additive only: inserts new people and updates changed fields on existing
    ones. Nobody is ever deleted or deactivated by an import, and a blank
    cell never erases a stored value."""
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Only .xlsx or .xls files are accepted.")
    data = await file.read()
    from app.services.employee.import_service import import_employees_from_bytes
    try:
        summary = await import_employees_from_bytes(db, data)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
    await datacache.bust_employees()
    return summary
