"""
Match an extracted (employee_id, name) against all_employee_data.

The primary path requires BOTH the employee_id AND the name to agree on the
SAME matcher row (id_and_name). employee_id is NOT globally unique — the AUH
and DXB teams have overlapping ID ranges, so the same ID can belong to two
different people; the name decides which one when several rows share an ID.

When the ID does not lead to a confident match (not found in the matcher, its
owner's name disagrees, or a shared ID can't be disambiguated), we fall back
to a global fuzzy NAME search across every employee — the sheet's name is
trusted over a possibly misread/OCR'd ID. That fallback only ever returns a
match when exactly ONE employee's name confidently agrees (or one clearly
outscores every other agreeing candidate); a tie or no agreement leaves the
sheet unmatched for a human to assign, same as before. Every name-fallback
match carries a note flagging the ID mismatch so the reviewer can verify it.

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
    NAME_FALLBACK = "name_fallback"    # ID failed to resolve; matched by name alone instead
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
    """Global fuzzy name search — used ONLY as a fallback once the ID has
    already failed to produce a confident match. Returns a single Employee
    only when exactly one agrees (or one clearly outscores the rest); a tie
    or no agreement returns None so we never guess."""
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

    # ---- both an ID and a real name are REQUIRED ----
    if not id_norm and not name_norm:
        return MatchResult(None, "No employee ID or name found on the sheet to match.",
                           MatchCode.NO_IDENTITY)
    if not id_norm:
        if name_norm and email_hint and _name_agrees(name_norm, email_hint.name):
            return MatchResult(
                email_hint,
                f'Matched inbox employee + sheet name ("{extracted_name}" → '
                f'"{email_hint.name}" · {email_hint.employee_id}{_loc(email_hint)}).',
                MatchCode.EMAIL_AND_NAME,
            )
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
        alt = await _match_by_name(db, name_norm)
        if alt:
            return MatchResult(
                alt, f'No employee with ID {id_norm} in the matcher, but the sheet name '
                     f'"{extracted_name}" matched "{alt.name}" · {alt.employee_id}{_loc(alt)} — '
                     "matched by name; please verify the ID.",
                MatchCode.NAME_FALLBACK,
            )
        return MatchResult(
            None,
            f'No employee with ID {id_norm} in the matcher (sheet name "{extracted_name}"). '
            "Add them to the matcher or assign the correct employee.",
            MatchCode.NO_MATCH,
        )

    # ---- single candidate: confirm the name agrees (short forms allowed) ----
    if len(candidates) == 1:
        emp = candidates[0]
        if _name_agrees(name_norm, emp.name):
            return MatchResult(
                emp, f"Matched by employee ID ({id_norm}) + name "
                     f'("{extracted_name}" → "{emp.name}"{_loc(emp)}).',
                MatchCode.ID_AND_NAME,
            )
        alt = await _match_by_name(db, name_norm)
        if alt:
            return MatchResult(
                alt, f'Employee ID {id_norm} belongs to "{emp.name}"{_loc(emp)}, but the sheet '
                     f'name "{extracted_name}" matched "{alt.name}" · {alt.employee_id}{_loc(alt)} '
                     "instead — matched by name; please verify the ID.",
                MatchCode.NAME_FALLBACK,
            )
        return MatchResult(
            None,
            f'Employee ID {id_norm} belongs to "{emp.name}"{_loc(emp)}, but the sheet name '
            f'"{extracted_name}" does not match it — not matched. Please assign the correct employee.',
            MatchCode.NO_MATCH,
        )

    # ---- shared ID: the name must single out exactly one person ----
    agreeing = [c for c in candidates if _name_agrees(name_norm, c.name)]
    if len(agreeing) == 1:
        emp = agreeing[0]
        return MatchResult(
            emp, f"Matched by employee ID ({id_norm}) + name "
                 f'("{extracted_name}" → "{emp.name}"{_loc(emp)}) — ID is shared across teams.',
            MatchCode.ID_AND_NAME,
        )
    if len(agreeing) > 1:
        # multiple plausibly agree — take a clear winner only if it dominates
        ranked = sorted(((_name_score(name_norm, c), c) for c in agreeing),
                        key=lambda t: t[0], reverse=True)
        best_score, best = ranked[0]
        second = ranked[1][0]
        if best_score - second >= 8:
            return MatchResult(
                best, f"Matched by employee ID ({id_norm}) + name "
                      f'("{extracted_name}" → "{best.name}"{_loc(best)}, {int(best_score)}% confidence).',
                MatchCode.ID_AND_NAME,
            )

    alt = await _match_by_name(db, name_norm)
    if alt:
        return MatchResult(
            alt, f'Employee ID {id_norm} is shared by multiple people and the sheet name didn\'t '
                 f'clearly pick one of them, but "{extracted_name}" matched "{alt.name}" · '
                 f'{alt.employee_id}{_loc(alt)} — matched by name; please verify the ID.',
            MatchCode.NAME_FALLBACK,
        )
    who = ", ".join(f"{c.name}{_loc(c)}" for c in candidates)
    return MatchResult(
        None,
        f'Employee ID {id_norm} is shared by {who}, and the sheet name "{extracted_name}" '
        "does not clearly identify one of them — not matched. Please assign the correct employee.",
        MatchCode.AMBIGUOUS_ID,
    )

