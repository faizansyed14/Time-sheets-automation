"""Files put straight into the File Vault without being extracted.

Every way a file enters the vault through the app (the vault's own upload, a move or a
copy) records ONE pipeline item for it: status needs_review, failure_code
VAULT_NOT_EXTRACTED, source_id "vault:<rel_path>". That single row is what keeps the
person from showing as "missing" on the Dashboard / Export and gives them an Extract
button in the Activity log - with no table of its own, no migration and no listing of
the vault on any page load. The row follows its file: deleted with it, moved with it,
and replaced by the real review item once the file is extracted.
"""
from __future__ import annotations

import calendar

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.pipeline_file import FailureCode, PipelineFile, PipelineStatus

PENDING_TAG = "__vault_pending__"
DETAIL = "Stored in the File Vault without being extracted. Press Extract to read it."


def source_id(rel: str) -> str:
    return f"vault:{rel}"


def parse_rel(rel: str) -> tuple[str, str, int, int, str] | None:
    """'<Manager>/<Employee>/<Month-Year>/<file>' -> (manager, employee, month, year, file)."""
    parts = [p for p in (rel or "").split("/") if p]
    if len(parts) < 4 or parts[0].startswith(("_", ".")):
        return None
    name, _, year = parts[2].rpartition("-")
    try:
        month = list(calendar.month_name).index(name)
        year_i = int(year)
    except ValueError:
        return None
    if not month:
        return None
    return parts[0], parts[1], month, year_i, parts[-1]


async def _folder_index(db: AsyncSession) -> dict[tuple[str, str], Employee]:
    from app.services import storage_provider as sp
    emps = (await db.execute(select(Employee))).scalars().all()
    return {sp.employee_vault_location(e.account_manager, e.name, e.aco_number, e.dco_number): e for e in emps}


async def register(db: AsyncSession, rel: str, index: dict | None = None, *, commit: bool = True) -> bool:
    """Record one vault file as "in vault, not extracted". Idempotent; False if the path
    is not an employee/month file or something already tracks this file."""
    parsed = parse_rel(rel)
    if not parsed:
        return False
    if (await db.execute(select(PipelineFile.id).where(PipelineFile.source_id == source_id(rel)).limit(1))).first():
        return False
    manager, folder, month, year, name = parsed
    index = index if index is not None else await _folder_index(db)
    emp = index.get((manager, folder))
    db.add(PipelineFile(
        filename=name, source_kind="vault", source_id=source_id(rel), thread_key=source_id(rel),
        attachment_id=PENDING_TAG, status=PipelineStatus.NEEDS_REVIEW,
        failure_code=FailureCode.VAULT_NOT_EXTRACTED, failure_detail=DETAIL,
        employee_id=emp.employee_id if emp else None, employee_name=emp.name if emp else folder,
        month=month, year=year,
        extraction_meta={"staged": {"employee_pk": emp.id if emp else None, "month": month, "year": year},
                         "vault_rel_path": rel},
    ))
    if commit:
        await db.commit()
    return True


def _pending_q():
    return select(PipelineFile).where(PipelineFile.attachment_id == PENDING_TAG)


async def forget(db: AsyncSession, rel: str, *, prefix: bool = False) -> int:
    """Drop the pending item(s) of a file (or of everything under a folder)."""
    cond = (PipelineFile.source_id.like(source_id(rel.rstrip("/")) + "/%") if prefix
            else PipelineFile.source_id == source_id(rel))
    res = await db.execute(delete(PipelineFile).where(PipelineFile.attachment_id == PENDING_TAG, cond))
    await db.commit()
    return res.rowcount or 0


async def relocate(db: AsyncSession, src: str, dst: str, *, copy: bool = False) -> None:
    """A file / folder moved or copied inside the vault: its pending items follow it."""
    src, dst = src.rstrip("/"), dst.rstrip("/")
    rows = (await db.execute(_pending_q().where(
        (PipelineFile.source_id == source_id(src)) | PipelineFile.source_id.like(source_id(src) + "/%")))).scalars().all()
    if not rows:
        return
    index = await _folder_index(db)
    for t in rows:
        old_rel = t.source_id[len("vault:"):]
        new_rel = dst + old_rel[len(src):]
        if not copy:
            await db.delete(t)
            await db.flush()
        await register(db, new_rel, index, commit=False)
    await db.commit()


async def pending_employee_pks(db: AsyncSession, month: int, year: int) -> set[str]:
    """Employees whose ONLY item this month is a vault file nobody extracted yet - the
    "In vault, not extracted" state. A plain indexed query; nothing touches the vault."""
    from app.services.pipeline.coverage import staged_employee_pk
    pk = staged_employee_pk()
    rows = (await db.execute(select(pk, PipelineFile.attachment_id).where(
        PipelineFile.month == month, PipelineFile.year == year, pk.is_not(None)))).all()
    pending = {p for p, tag in rows if tag == PENDING_TAG}
    real = {p for p, tag in rows if tag != PENDING_TAG}
    return pending - real
