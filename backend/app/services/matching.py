"""
Match an extracted (employee_id, name) against all_employee_data.

employee_id is NOT globally unique — the AUH and DXB teams have overlapping ID
ranges, so the same ID can belong to two different people (with different
names). Matching therefore always considers BOTH the ID and the name:

  1. employee_id lookup:
       - one candidate  -> confirm with the name when one was extracted
                           (a strong name disagreement is flagged, not silently
                            accepted)
       - many candidates (AUH + DXB share the ID) -> disambiguate by exact
         then fuzzy name; if the name can't pick a side, the match is
         AMBIGUOUS and the file goes to the pipeline tracker instead of being
         filed under the wrong person
  2. exact (case-insensitive) name
  3. fuzzy name (rapidfuzz) above threshold  -> handles "Mohd Ali" vs "Mohammed Ali"

Returns a MatchResult carrying the matched Employee (or None), a
human-readable note, and a machine code the pipeline tracker uses.
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee

FUZZY_THRESHOLD = 82
# Below this, a name printed on the sheet is considered a different person
# than the one the ID points to (used to flag ID/name disagreements).
NAME_AGREEMENT_THRESHOLD = 60


class MatchCode:
    ID_AND_NAME = "id_and_name"        # id + name both agree (strongest)
    ID_ONLY = "id_only"                # id matched, no name on the sheet
    ID_NAME_MISMATCH = "id_name_mismatch"  # id matched but name disagrees -> review
    NAME_EXACT = "name_exact"
    NAME_FUZZY = "name_fuzzy"
    AMBIGUOUS_ID = "ambiguous_id"      # shared AUH/DXB id, name can't disambiguate
    NO_MATCH = "no_match"
    NO_IDENTITY = "no_identity"        # nothing extracted to match on


@dataclass
class MatchResult:
    employee: Employee | None
    note: str
    code: str


def _loc(e: Employee) -> str:
    return f" [{e.location}]" if e.location else ""


async def match_employee(
    db: AsyncSession, extracted_id: str | None, extracted_name: str | None
) -> MatchResult:
    name_norm = (extracted_name or "").strip().lower()

    # ---- 1) employee_id (may return several rows: AUH/DXB share IDs) ----
    if extracted_id and extracted_id.strip():
        candidates = (
            await db.execute(select(Employee).where(Employee.employee_id == extracted_id.strip()))
        ).scalars().all()

        if len(candidates) == 1:
            emp = candidates[0]
            if not name_norm:
                return MatchResult(emp, f"Matched by employee ID ({extracted_id}); no name on sheet.",
                                   MatchCode.ID_ONLY)
            score = fuzz.WRatio(name_norm, emp.name.strip().lower())
            if emp.name.strip().lower() == name_norm or score >= NAME_AGREEMENT_THRESHOLD:
                return MatchResult(emp, f"Matched by employee ID ({extracted_id}) + name "
                                        f"({extracted_name} ≈ {emp.name}).", MatchCode.ID_AND_NAME)
            return MatchResult(
                emp,
                f'ID {extracted_id} belongs to "{emp.name}"{_loc(emp)} but the sheet says '
                f'"{extracted_name}" — please confirm the right person.',
                MatchCode.ID_NAME_MISMATCH,
            )

        if len(candidates) > 1:
            # Shared ID (AUH + DXB). The name decides which person this is.
            if name_norm:
                exact = [c for c in candidates if c.name.strip().lower() == name_norm]
                if len(exact) == 1:
                    emp = exact[0]
                    return MatchResult(emp, f"Matched by employee ID ({extracted_id}) + exact name "
                                            f"({emp.name}{_loc(emp)}) — ID is shared across teams.",
                                       MatchCode.ID_AND_NAME)
                scored = sorted(
                    ((fuzz.WRatio(name_norm, c.name.strip().lower()), c) for c in candidates),
                    key=lambda t: t[0], reverse=True,
                )
                best_score, best = scored[0]
                second_score = scored[1][0] if len(scored) > 1 else 0
                if best_score >= FUZZY_THRESHOLD and best_score - second_score >= 5:
                    return MatchResult(
                        best,
                        f'Matched by employee ID ({extracted_id}) + fuzzy name: "{extracted_name}" → '
                        f'"{best.name}"{_loc(best)} ({int(best_score)}% confidence) — ID is shared across teams.',
                        MatchCode.ID_AND_NAME,
                    )
            shared = ", ".join(f"{c.name}{_loc(c)}" for c in candidates)
            return MatchResult(
                None,
                f"Employee ID {extracted_id} is shared by multiple people ({shared}) and the name "
                f'on the sheet ("{extracted_name or "none"}") does not clearly identify one of them.',
                MatchCode.AMBIGUOUS_ID,
            )

    if not name_norm:
        return MatchResult(None, "No employee ID or name found on the sheet to match.",
                           MatchCode.NO_IDENTITY)

    # ---- 2) exact name ----
    rows = (
        await db.execute(select(Employee).where(func.lower(func.trim(Employee.name)) == name_norm))
    ).scalars().all()
    if len(rows) == 1:
        return MatchResult(rows[0], f"Matched by exact name ({extracted_name}).", MatchCode.NAME_EXACT)
    if len(rows) > 1:
        shared = ", ".join(f"{r.employee_id}{_loc(r)}" for r in rows)
        return MatchResult(None, f'Multiple employees are named "{extracted_name}" ({shared}); '
                                 "an employee ID is needed to tell them apart.", MatchCode.AMBIGUOUS_ID)

    # ---- 3) fuzzy name ----
    all_emps = (await db.execute(select(Employee))).scalars().all()
    if not all_emps:
        return MatchResult(None, "Employee matcher list is empty.", MatchCode.NO_MATCH)
    choices = {f"{e.id}": e for e in all_emps}
    best = process.extractOne(
        extracted_name, {k: v.name for k, v in choices.items()}, scorer=fuzz.WRatio
    )
    if best and best[1] >= FUZZY_THRESHOLD:
        matched = choices[best[2]]
        return MatchResult(
            matched,
            f'Fuzzy match: "{extracted_name}" → "{matched.name}"{_loc(matched)} '
            f"({int(best[1])}% confidence).",
            MatchCode.NAME_FUZZY,
        )

    return MatchResult(None, f'No employee matcher entry found for "{extracted_name}".',
                       MatchCode.NO_MATCH)
