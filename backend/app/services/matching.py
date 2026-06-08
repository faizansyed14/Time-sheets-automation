"""
Match an extracted (employee_id, name) against all_employee_data.

Strategy, in order of confidence:
  1. exact employee_id
  2. exact (case-insensitive) name
  3. fuzzy name (rapidfuzz) above threshold  -> handles "Mohd Ali" vs "Mohammed Ali"
Returns the matched Employee (or None) and a human-readable note.
"""
from __future__ import annotations

from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee

FUZZY_THRESHOLD = 82


async def match_employee(
    db: AsyncSession, extracted_id: str | None, extracted_name: str | None
) -> tuple[Employee | None, str]:
    # 1) exact id
    if extracted_id:
        row = (
            await db.execute(select(Employee).where(Employee.employee_id == extracted_id.strip()))
        ).scalar_one_or_none()
        if row:
            return row, f"Matched by employee ID ({extracted_id})."

    if not extracted_name:
        return None, "No employee ID or name to match."

    name_norm = extracted_name.strip().lower()

    # 2) exact name
    row = (
        await db.execute(
            select(Employee).where(func.lower(func.trim(Employee.name)) == name_norm)
        )
    ).scalar_one_or_none()
    if row:
        return row, f"Matched by exact name ({extracted_name})."

    # 3) fuzzy name
    all_emps = (await db.execute(select(Employee))).scalars().all()
    if not all_emps:
        return None, "Employee matcher list is empty."
    choices = {e.name: e for e in all_emps}
    best = process.extractOne(extracted_name, list(choices.keys()), scorer=fuzz.WRatio)
    if best and best[1] >= FUZZY_THRESHOLD:
        matched = choices[best[0]]
        return matched, f'Fuzzy match: "{extracted_name}" → "{matched.name}" ({int(best[1])}% confidence).'

    return None, f'No employee_matcher match for "{extracted_name}".'
