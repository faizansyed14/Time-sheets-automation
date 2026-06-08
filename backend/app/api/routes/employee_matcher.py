"""CRUD for all_employee_data (the Employee Matcher list), exposed to the UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.employee import Employee
from app.schemas import EmployeeIn, EmployeeOut

router = APIRouter(prefix="/employee-matcher", tags=["employee-matcher"])


def _out(e: Employee) -> EmployeeOut:
    return EmployeeOut(
        id=e.id, employee_id=e.employee_id, name=e.name,
        dco_number=e.dco_number, account_manager=e.account_manager,
        employee_email_id=e.employee_email_id,
    )


@router.get("", response_model=list[EmployeeOut])
async def list_employees(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Employee).order_by(Employee.name))).scalars().all()
    return [_out(e) for e in rows]


@router.post("", response_model=EmployeeOut, status_code=201)
async def create_employee(body: EmployeeIn, db: AsyncSession = Depends(get_db)):
    dup = (await db.execute(select(Employee).where(Employee.employee_id == body.employee_id))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, f"Employee ID {body.employee_id} already exists.")
    e = Employee(**body.model_dump())
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return _out(e)


@router.put("/{pk}", response_model=EmployeeOut)
async def update_employee(pk: str, body: EmployeeIn, db: AsyncSession = Depends(get_db)):
    e = (await db.execute(select(Employee).where(Employee.id == pk))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    # block id collision with a different row
    other = (await db.execute(select(Employee).where(Employee.employee_id == body.employee_id))).scalar_one_or_none()
    if other and other.id != pk:
        raise HTTPException(409, f"Employee ID {body.employee_id} already used by another row.")
    for k, v in body.model_dump().items():
        setattr(e, k, v)
    await db.commit()
    await db.refresh(e)
    return _out(e)


@router.delete("/{pk}")
async def delete_employee(pk: str, db: AsyncSession = Depends(get_db)):
    e = (await db.execute(select(Employee).where(Employee.id == pk))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    await db.delete(e)
    await db.commit()
    return {"deleted": pk}
