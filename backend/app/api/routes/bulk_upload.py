"""
Bulk roster upload — ONE sheet listing MANY employees.

A separate route from /upload on purpose. /upload's contract is "each file is
one submission for one employee(+month)"; a roster inverts that, and the two
need different prompts, different validation and a different confirmation
step. Keeping them apart means neither can regress the other.

    POST /bulk-upload/preview   read the roster, return what was found,
                                stage NOTHING. Lets the uploader confirm the
                                headcount and see per-row problems before
                                committing 100 review items to the pipeline.
    POST /bulk-upload           read AND stage: one review item per employee,
                                each carrying the whole roster as its source
                                file, all awaiting Accept in Compare & Fix.

Nothing files without review — identical to every other intake path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import datacache
from app.core.database import get_db
from app.services.bulk_roster.roster_cache import extract_roster_cached
from app.services.bulk_roster.roster_codes import distribute_days, verify_row_totals
from app.services.bulk_roster.roster_stage import stage_roster

router = APIRouter(prefix="/bulk-upload", tags=["bulk-upload"])


class RosterPreviewRow(BaseModel):
    sr_no: int
    name: str
    title: str | None = None
    location: str | None = None
    confirmation: str | None = None
    stated_leave_days: float | None = None
    stated_billing_days: float | None = None
    leave_days_read: int = 0
    working_days_read: int = 0
    weekend_days_read: int = 0
    uncertain_days: int = 0
    # Whether this employee resolves to someone in the Employee Matcher.
    matched_name: str | None = None
    matched_employee_id: str | None = None
    issues: list[str] = []


class RosterPreview(BaseModel):
    filename: str
    method: str
    month: int | None = None
    year: int | None = None
    calendar_days: int | None = None
    agency: str | None = None
    headcount: int = 0
    matched: int = 0
    unmatched: int = 0
    flagged: int = 0
    llm_calls: int = 0
    issues: list[str] = []
    rows: list[RosterPreviewRow] = []


class BulkStageResult(BaseModel):
    filename: str
    headcount: int
    staged: int
    matched: int
    unmatched: list[str] = []
    flagged: int = 0
    month: int | None = None
    year: int | None = None
    method: str = ""
    issues: list[str] = []


async def _read_upload(file: UploadFile) -> tuple[str, str, bytes]:
    data = await file.read()
    if not data:
        raise HTTPException(400, "The uploaded file was empty.")
    return (file.filename or "roster",
            file.content_type or "application/octet-stream", data)


@router.post("/preview", response_model=RosterPreview)
async def preview_bulk_roster(
    file: UploadFile = File(...),
    month: int | None = Form(default=None),
    year: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Read the roster and report what WOULD be staged. Writes nothing."""
    from sqlalchemy import select

    from app.models.employee import Employee
    from app.services.pipeline import matching

    filename, _ct, data = await _read_upload(file)
    try:
        doc = await extract_roster_cached(filename, data, month=month, year=year)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:      # vision not configured
        raise HTTPException(503, str(e))

    # Fetched ONCE and reused for every row below — see roster_stage.py's
    # build_groups() for why (match_employee() otherwise re-fetches the whole
    # employee table per roster row). Active only — a deactivated row must
    # never compete with the real, current employee.
    all_employees = (await db.execute(
        select(Employee).where(Employee.active.is_(True))
    )).scalars().all()

    rows: list[RosterPreviewRow] = []
    matched_count = 0
    for r in doc.rows:
        fields, uncertain, day_issues = distribute_days(r.day_codes, doc.month, doc.year)
        issues = list(r.issues) + day_issues + verify_row_totals(
            r.day_codes, r.stated_leave_days, r.stated_billing_days, doc.calendar_days)
        m = await matching.match_employee(db, r.employee_id, r.name, all_employees=all_employees)
        if m.employee:
            matched_count += 1
        leave = sum(len(v) for k, v in fields.items()
                    if k not in ("working_days", "weekend_days"))
        rows.append(RosterPreviewRow(
            sr_no=r.sr_no, name=r.name, title=r.title, location=r.location,
            confirmation=r.confirmation,
            stated_leave_days=r.stated_leave_days,
            stated_billing_days=r.stated_billing_days,
            leave_days_read=leave,
            working_days_read=len(fields.get("working_days", [])),
            weekend_days_read=len(fields.get("weekend_days", [])),
            uncertain_days=len(uncertain),
            matched_name=m.employee.name if m.employee else None,
            matched_employee_id=m.employee.employee_id if m.employee else None,
            issues=issues,
        ))

    return RosterPreview(
        filename=filename, method=doc.method, month=doc.month, year=doc.year,
        calendar_days=doc.calendar_days, agency=doc.agency,
        headcount=doc.headcount, matched=matched_count,
        unmatched=doc.headcount - matched_count,
        flagged=sum(1 for r in rows if r.issues),
        llm_calls=doc.llm_calls, issues=doc.issues, rows=rows,
    )


@router.post("", response_model=BulkStageResult)
async def upload_bulk_roster(
    file: UploadFile = File(...),
    month: int | None = Form(default=None),
    year: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Read the roster and stage one review item per employee on it."""
    filename, content_type, data = await _read_upload(file)
    try:
        doc = await extract_roster_cached(filename, data, month=month, year=year)
        result = await stage_roster(
            db, filename=filename, content_type=content_type, data=data, doc=doc)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    await datacache.bust_pipeline()
    return BulkStageResult(
        filename=filename,
        headcount=result["headcount"],
        staged=len(result["staged"]),
        matched=result["matched"],
        unmatched=result["unmatched"],
        flagged=result["flagged"],
        month=result["month"], year=result["year"], method=result["method"],
        issues=result["issues"],
    )
