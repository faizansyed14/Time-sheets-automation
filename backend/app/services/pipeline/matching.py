"""
Match an extracted (employee_id, name) against all_employee_data.

Matching is STRICT: a confident match requires BOTH the employee_id AND the
name to agree on the SAME matcher row. We never match on the ID alone or the
name alone, and we never search the whole table by name — that would risk
filing a sheet under the wrong person. Anything that isn't a clean ID+name
agreement returns no match (employee=None) and is flagged in the pipeline
tracker for a human to assign.

employee_id is NOT globally unique — the AUH and DXB teams have overlapping ID
ranges, so the same ID can belong to two different people. The name decides
which one; if it can't, the file is flagged as ambiguous rather than guessed.

Returns a MatchResult carrying the matched Employee (or None), a human-readable
note, and a machine code the pipeline tracker uses.
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee

# A fuzzy name match (used ONLY to confirm the sheet name against the row(s) the
# ID already points to — never as a global name search) must clear both an
# overall score and a token-overlap floor, so "Mohd Ali" ≈ "Mohammed Ali" is
# accepted while "John Doe" vs "John Murphy Ibanez" is not.
FUZZY_THRESHOLD = 82
FUZZY_TOKEN_FLOOR = 70

# Obvious placeholder / example values an LLM may emit when it cannot read the
# sheet. These must NEVER be matched to a real employee.
_PLACEHOLDER_NAMES = {
    "john doe", "jane doe", "john smith", "first last", "firstname lastname",
    "employee name", "full name", "name", "employee", "test", "testing", "sample",
    "n/a", "na", "none", "unknown", "xxxx", "xxx", "abc", "string",
}


def _is_placeholder_name(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return True
    if n in _PLACEHOLDER_NAMES:
        return True
    # all non-letters (e.g. "----", "n/a.") or a single short token like "x"
    letters = [c for c in n if c.isalpha()]
    return len(letters) < 2


class MatchCode:
    ID_AND_NAME = "id_and_name"        # id + name both agree on one row (the ONLY match)
    AMBIGUOUS_ID = "ambiguous_id"      # shared AUH/DXB id, name can't disambiguate
    NO_MATCH = "no_match"              # id or name missing / not found / disagree
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
    name_is_placeholder = bool(name_norm) and _is_placeholder_name(name_norm)
    if name_is_placeholder:
        name_norm = ""           # a placeholder name counts as no name
    id_norm = (extracted_id or "").strip()

    # ---- both an ID and a real name are REQUIRED ----
    if not id_norm and not name_norm:
        return MatchResult(None, "No employee ID or name found on the sheet to match.",
                           MatchCode.NO_IDENTITY)
    if not id_norm:
        return MatchResult(
            None,
            f'Only a name ("{extracted_name}") was found — an employee ID is also required '
            "to match. Please assign the correct employee.",
            MatchCode.NO_MATCH,
        )
    if not name_norm:
        why = "only a placeholder name" if name_is_placeholder else "no employee name"
        return MatchResult(
            None,
            f'Employee ID {id_norm} was found but {why} — both the ID and the name are '
            "required to match. Please assign the correct employee.",
            MatchCode.NO_MATCH,
        )

    # ---- look up by ID (AUH/DXB may share an ID -> several candidates) ----
    candidates = (
        await db.execute(select(Employee).where(Employee.employee_id == id_norm))
    ).scalars().all()
    if not candidates:
        return MatchResult(
            None,
            f'No employee with ID {id_norm} in the matcher (sheet name "{extracted_name}"). '
            "Add them to the matcher or assign the correct employee.",
            MatchCode.NO_MATCH,
        )

    # ---- the name must agree with exactly one candidate (exact, else strong fuzzy) ----
    exact = [c for c in candidates if c.name.strip().lower() == name_norm]
    if len(exact) == 1:
        emp = exact[0]
        return MatchResult(
            emp, f"Matched by employee ID ({id_norm}) + name ({emp.name}{_loc(emp)}).",
            MatchCode.ID_AND_NAME,
        )
    if len(exact) > 1:
        return MatchResult(
            None, f'Employee ID {id_norm} + name "{extracted_name}" matches multiple rows — ambiguous.',
            MatchCode.AMBIGUOUS_ID,
        )

    # fuzzy ONLY against the ID's own candidate rows (tolerate OCR / abbreviations)
    scored = sorted(
        ((fuzz.WRatio(name_norm, c.name.strip().lower()),
          fuzz.token_sort_ratio(name_norm, c.name.strip().lower()), c) for c in candidates),
        key=lambda t: t[0], reverse=True,
    )
    best_score, best_token, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    if (best_score >= FUZZY_THRESHOLD and best_token >= FUZZY_TOKEN_FLOOR
            and (len(candidates) == 1 or best_score - second_score >= 5)):
        return MatchResult(
            best,
            f'Matched by employee ID ({id_norm}) + name "{extracted_name}" → '
            f'"{best.name}"{_loc(best)} ({int(best_score)}% confidence).',
            MatchCode.ID_AND_NAME,
        )

    # ID exists but the name does NOT agree with it -> NOT matched, flag for assignment.
    who = ", ".join(f"{c.name}{_loc(c)}" for c in candidates)
    code = MatchCode.AMBIGUOUS_ID if len(candidates) > 1 else MatchCode.NO_MATCH
    return MatchResult(
        None,
        f'Employee ID {id_norm} belongs to {who}, but the sheet name "{extracted_name}" '
        "does not match — not matched. Please assign the correct employee.",
        code,
    )

