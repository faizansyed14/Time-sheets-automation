"""Short-lived cache so Preview and Stage — two independent HTTP requests —
don't each pay for their own fresh vision extraction of the same file.

Preview reads a roster to show the uploader what would be staged; Stage
reads it again to actually create the review items. Before this module,
those were two SEPARATE calls to extract_roster() — for the vision path
(PDF/image/DOCX), that meant paying for the census + grid calls TWICE per
upload, and being independently exposed to whatever normal variance a vision
model has TWICE. That is exactly how "Preview succeeded, Stage failed" (or
the reverse) can happen on the SAME file for no reason related to the file
itself — two separate coin-flips instead of one.

Keyed by the file's own content hash (not filename — a byte-identical
re-upload under a different name should still hit) plus the month/year
override, since that changes the result. Short TTL: long enough for a normal
preview-then-stage flow (a person reading the numbers before clicking
Stage), short enough that staleness is never a real concern — the worst
case on expiry is one extra vision call, never wrong data.
"""
from __future__ import annotations

from app.core.cache import cache
from app.services.bulk_roster.roster_extract import extract_roster
from app.services.bulk_roster.roster_stage import roster_digest
from app.services.bulk_roster.roster_types import RosterDoc, RosterRow

_TTL_SECONDS = 900  # 15 minutes — a normal preview-then-stage gap, not a long-term cache


def _cache_key(data: bytes, month: int | None, year: int | None) -> str:
    return f"roster-extract:{roster_digest(data)}:{month or 0}:{year or 0}"


def _doc_to_cache(doc: RosterDoc) -> dict:
    return {
        "month": doc.month, "year": doc.year, "calendar_days": doc.calendar_days,
        "title": doc.title, "agency": doc.agency,
        "method": doc.method, "model": doc.model, "llm_calls": doc.llm_calls,
        "issues": list(doc.issues),
        "approval_signature_detected": doc.approval_signature_detected,
        "approval_signature_detail": doc.approval_signature_detail,
        "rows": [
            {
                "sr_no": r.sr_no, "name": r.name, "title": r.title, "location": r.location,
                "employee_id": r.employee_id, "confirmation": r.confirmation,
                "stated_leave_days": r.stated_leave_days,
                "stated_billing_days": r.stated_billing_days,
                # JSON object keys are always strings — restored to int in
                # _doc_from_cache below.
                "day_codes": {str(k): v for k, v in r.day_codes.items()},
                "issues": list(r.issues),
            }
            for r in doc.rows
        ],
    }


def _doc_from_cache(data: dict) -> RosterDoc:
    doc = RosterDoc(
        month=data.get("month"), year=data.get("year"),
        calendar_days=data.get("calendar_days"),
        title=data.get("title"), agency=data.get("agency"),
        method=data.get("method") or "", model=data.get("model"),
        llm_calls=data.get("llm_calls") or 0,
        issues=list(data.get("issues") or []),
        approval_signature_detected=bool(data.get("approval_signature_detected")),
        approval_signature_detail=data.get("approval_signature_detail"),
    )
    doc.rows = [
        RosterRow(
            sr_no=r["sr_no"], name=r["name"], title=r.get("title"),
            location=r.get("location"), employee_id=r.get("employee_id"),
            confirmation=r.get("confirmation"),
            stated_leave_days=r.get("stated_leave_days"),
            stated_billing_days=r.get("stated_billing_days"),
            day_codes={int(k): v for k, v in (r.get("day_codes") or {}).items()},
            issues=list(r.get("issues") or []),
        )
        for r in data.get("rows") or []
    ]
    return doc


async def extract_roster_cached(
    filename: str, data: bytes, *, month: int | None = None, year: int | None = None,
) -> RosterDoc:
    """Same contract as extract_roster() — raises ValueError/RuntimeError the
    same way on a genuine read failure — but a second call with the SAME
    file bytes and period override, shortly after the first, returns the
    already-read result instead of re-running vision from scratch.

    Deliberately only caches a SUCCESSFUL read: extract_roster() raising is
    never cached, so a failed attempt never poisons a retry — the very next
    call just tries fresh, exactly as it did before this cache existed."""
    key = _cache_key(data, month, year)
    try:
        cached = await cache.get(key)
    except Exception:
        cached = None
    if isinstance(cached, dict) and cached.get("rows"):
        return _doc_from_cache(cached)

    doc = await extract_roster(filename, data, month=month, year=year)

    try:
        await cache.set(key, _doc_to_cache(doc), ttl=_TTL_SECONDS)
    except Exception:
        pass  # caching is best-effort — a cache write failure must never fail the request
    return doc
