"""
Match an extracted (employee_id, name) against all_employee_data.

Client timesheets often carry an Emp No from the *client's* HR system (e.g.
FDF 109427) that does not exist in our matcher. The sheet **name** is the
primary key — we always try a global fuzzy name search first when a real name
is present. The extracted employee_id is kept only as reference text for the
reviewer; it does not gate matching.

When the name alone cannot uniquely identify someone, we fall back to looking
up the extracted ID. employee_id is NOT globally unique — AUH and DXB share
ID ranges, so the name must still agree when several rows share an ID.

Returns a MatchResult carrying the matched Employee (or None), a human-readable
note, and a machine code the pipeline tracker uses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee

# A fuzzy name match — used both to confirm the sheet name against the row(s)
# the ID points to, and as the global fallback search when the ID fails — must
# clear both an overall score and a token-overlap floor, so "Mohd Ali" ≈
# "Mohammed Ali" is accepted while "John Doe" vs "John Murphy Ibanez" is not.
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


# --------------------------------------------------------------------------- #
# Name agreement
#
# Real timesheets carry short forms of the matcher's full name: dropped middle
# names ("Abdul Ghani" for "Abdul Syed Ghani"), initials ("A. Ghani"), and
# abbreviations ("Mohd Ali" for "Mohammed Ali"). A confident match is accepted
# when EITHER:
#   (a) the classic fuzzy path passes (handles abbreviations / typos), OR
#   (b) the sheet name is an abbreviation-aware token SUBSET of the full name
#       (handles dropped middle names + initials).
# The subset path requires the surname (or ≥2 tokens) to line up, so a shared
# first name alone ("Abdul" → "Abdul Syed Ghani") is NOT accepted.
# --------------------------------------------------------------------------- #
def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t]


def _token_match(a: str, b: str) -> bool:
    """Do two name tokens plausibly refer to the same name part?"""
    if a == b:
        return True
    # abbreviation by prefix: "abd"→"abdul", "ghani"→"ghanim" (len>=3 both sides)
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    # initial: "a"→"abdul"
    if len(a) == 1 and b[:1] == a:
        return True
    if len(b) == 1 and a[:1] == b:
        return True
    return False


def _subset_compatible(e_tokens: list[str], c_tokens: list[str]) -> bool:
    """True when the shorter name's tokens are all present (abbrev/initial aware)
    in the longer name — i.e. one is a short form of the other."""
    short, long = (e_tokens, c_tokens) if len(e_tokens) <= len(c_tokens) else (c_tokens, e_tokens)
    if not short or not long:
        return False
    used = [False] * len(long)
    matched = 0
    for ta in short:
        for j, tb in enumerate(long):
            if not used[j] and _token_match(ta, tb):
                used[j] = True
                matched += 1
                break
    if matched != len(short):
        return False                       # a token in the shorter name isn't in the longer one
    if len(short) == 1:
        return _token_match(short[0], long[-1])   # one token => must be the surname
    return True                            # 2+ tokens fully contained => same person


def _name_agrees(extracted: str, candidate: str) -> bool:
    a, b = extracted.strip().lower(), candidate.strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    # (a) classic fuzzy path — handles abbreviations / OCR typos
    if fuzz.WRatio(a, b) >= FUZZY_THRESHOLD and fuzz.token_sort_ratio(a, b) >= FUZZY_TOKEN_FLOOR:
        return True
    # (b) abbreviation-aware token subset — handles dropped names + initials
    return _subset_compatible(_tokens(a), _tokens(b))


def _name_score(extracted: str, candidate: str) -> float:
    """Confidence used to rank candidates of a SHARED id."""
    a, b = extracted.strip().lower(), candidate.strip().lower()
    score = float(fuzz.WRatio(a, b))
    if _subset_compatible(_tokens(a), _tokens(b)):
        score = max(score, 93.0)
    return score


class MatchCode:
    ID_AND_NAME = "id_and_name"
    EMAIL_AND_NAME = "email_and_name"  # sender / AI employee + sheet name, no id on PDF
    NAME_PRIMARY = "name_primary"      # matched by sheet name; client ID ignored or differs
    NAME_FALLBACK = "name_fallback"    # alias kept for older rows; same as name_primary
    AMBIGUOUS_ID = "ambiguous_id"
    NO_MATCH = "no_match"              # id or name missing / not found / disagree
    NO_IDENTITY = "no_identity"        # nothing extracted to match on


@dataclass
class MatchResult:
    employee: Employee | None
    note: str
    code: str


def _loc(e: Employee) -> str:
    return f" [{e.location}]" if e.location else ""


async def _match_by_name(db: AsyncSession, name_norm: str) -> Employee | None:
    """Global fuzzy name search — primary matcher when the sheet carries a
    real name. Returns a single Employee only when exactly one agrees (or one
    clearly outscores the rest); a tie or no agreement returns None."""
    all_employees = (await db.execute(select(Employee))).scalars().all()
    agreeing = [e for e in all_employees if _name_agrees(name_norm, e.name)]
    if len(agreeing) == 1:
        return agreeing[0]
    if len(agreeing) > 1:
        ranked = sorted(((_name_score(name_norm, e.name), e) for e in agreeing),
                        key=lambda t: t[0], reverse=True)
        best_score, best = ranked[0]
        second = ranked[1][0]
        if best_score - second >= 8:
            return best
    return None


def _id_on_sheet_matches(emp: Employee, id_norm: str) -> bool:
    return (emp.employee_id or "").strip().upper() == id_norm.upper()


def _name_match_result(
    emp: Employee,
    extracted_name: str | None,
    id_norm: str,
    *,
    email_hint: Employee | None = None,
) -> MatchResult:
    """Build a result after a confident name match."""
    if email_hint and emp.id == email_hint.id:
        return MatchResult(
            emp,
            f'Matched inbox employee + sheet name ("{extracted_name}" → '
            f'"{emp.name}" · {emp.employee_id}{_loc(emp)}).',
            MatchCode.EMAIL_AND_NAME,
        )
    if id_norm and _id_on_sheet_matches(emp, id_norm):
        return MatchResult(
            emp,
            f'Matched by name ("{extracted_name}" → "{emp.name}"{_loc(emp)}).',
            MatchCode.ID_AND_NAME,
        )
    if id_norm:
        return MatchResult(
            emp,
            f'Matched by name ("{extracted_name}" → "{emp.name}" · '
            f'{emp.employee_id}{_loc(emp)}). Sheet shows client ID {id_norm} '
            f'— not used for matching.',
            MatchCode.NAME_PRIMARY,
        )
    return MatchResult(
        emp,
        f'Matched by name ("{extracted_name}" → "{emp.name}" · '
        f'{emp.employee_id}{_loc(emp)}).',
        MatchCode.NAME_PRIMARY,
    )


async def _match_by_id(
    db: AsyncSession,
    id_norm: str,
    name_norm: str,
    extracted_name: str | None,
) -> MatchResult | None:
    """Secondary path when the name alone did not uniquely match."""
    from sqlalchemy import func
    candidates = (
        await db.execute(select(Employee).where(
            func.upper(Employee.employee_id) == id_norm.upper()))
    ).scalars().all()
    if not candidates:
        return None

    if len(candidates) == 1:
        emp = candidates[0]
        if _name_agrees(name_norm, emp.name):
            return MatchResult(
                emp,
                f'Matched by employee ID ({id_norm}) + name '
                f'("{extracted_name}" → "{emp.name}"{_loc(emp)}).',
                MatchCode.ID_AND_NAME,
            )
        return MatchResult(
            None,
            f'Employee ID {id_norm} belongs to "{emp.name}"{_loc(emp)}, but the sheet name '
            f'"{extracted_name}" does not match it — not matched. Please assign the correct employee.',
            MatchCode.NO_MATCH,
        )

    agreeing = [c for c in candidates if _name_agrees(name_norm, c.name)]
    if len(agreeing) == 1:
        emp = agreeing[0]
        return MatchResult(
            emp,
            f'Matched by employee ID ({id_norm}) + name '
            f'("{extracted_name}" → "{emp.name}"{_loc(emp)}) — ID is shared across teams.',
            MatchCode.ID_AND_NAME,
        )
    if len(agreeing) > 1:
        ranked = sorted(((_name_score(name_norm, c), c) for c in agreeing),
                        key=lambda t: t[0], reverse=True)
        best_score, best = ranked[0]
        second = ranked[1][0]
        if best_score - second >= 8:
            return MatchResult(
                best,
                f'Matched by employee ID ({id_norm}) + name '
                f'("{extracted_name}" → "{best.name}"{_loc(best)}, '
                f'{int(best_score)}% confidence).',
                MatchCode.ID_AND_NAME,
            )

    who = ", ".join(f"{c.name}{_loc(c)}" for c in candidates)
    return MatchResult(
        None,
        f'Employee ID {id_norm} is shared by {who}, and the sheet name "{extracted_name}" '
        "does not clearly identify one of them — not matched. Please assign the correct employee.",
        MatchCode.AMBIGUOUS_ID,
    )


async def match_employee(
    db: AsyncSession,
    extracted_id: str | None,
    extracted_name: str | None,
    *,
    email_hint: Employee | None = None,
) -> MatchResult:
    name_norm = (extracted_name or "").strip().lower()
    name_is_placeholder = bool(name_norm) and _is_placeholder_name(name_norm)
    if name_is_placeholder:
        name_norm = ""           # a placeholder name counts as no name
    id_norm = (extracted_id or "").strip()

    if not id_norm and not name_norm:
        return MatchResult(None, "No employee ID or name found on the sheet to match.",
                           MatchCode.NO_IDENTITY)

    # ---- name first — client Emp No is not our matcher ID ----
    if name_norm:
        if email_hint and _name_agrees(name_norm, email_hint.name):
            return _name_match_result(
                email_hint, extracted_name, id_norm, email_hint=email_hint)
        by_name = await _match_by_name(db, name_norm)
        if by_name is not None:
            return _name_match_result(by_name, extracted_name, id_norm)

    if not id_norm:
        return MatchResult(
            None,
            f'Only a name ("{extracted_name}") was found and it did not uniquely match any '
            "employee. Please assign the correct employee.",
            MatchCode.NO_MATCH,
        )
    if not name_norm:
        why = "only a placeholder name" if name_is_placeholder else "no employee name"
        return MatchResult(
            None,
            f'Employee ID {id_norm} was found but {why} — a readable name is '
            "required to match. Please assign the correct employee.",
            MatchCode.NO_MATCH,
        )

    # ---- ID fallback when the name alone was ambiguous or missing ----
    by_id = await _match_by_id(db, id_norm, name_norm, extracted_name)
    if by_id is not None:
        return by_id
    return MatchResult(
        None,
        f'No employee with ID {id_norm} in the matcher (sheet name "{extracted_name}"). '
        "Add them to the matcher or assign the correct employee.",
        MatchCode.NO_MATCH,
    )

