"""Per-sheet vision extract (classify → one extract call per sheet → engine fallback).

No batching. One extraction prompt per sheet (see prompts.extract_prompt).
"""
from __future__ import annotations

from app.core.config import settings
from app.models.email_message import EmailMessage
from app.services.extract_email.prompts import extract_prompt
from app.services.extract_email.sheet_normalizer import (
    boost_sheet_from_hints,
    demote_financial_sheet,
    normalize_sheet,
    sanitize_body_sheet,
)
from app.services.extract_email.types import SheetUnit


# Max extraction calls in flight at once (see analyse_units) — sheets are
# independent so they run concurrently; the cap respects provider rate limits.
EXTRACT_CONCURRENCY = 4


def is_native_file_unit(u: SheetUnit, provider: str) -> bool:
    from app.services.extraction import vision_client
    return provider == "openai" and u.ftype in vision_client.NATIVE_FILE_TYPES


async def engine_sheet(unit: SheetUnit) -> dict:
    """Fallback: run one sheet through the standard extraction engine."""
    from app.services.extraction import get_extraction_engine

    try:
        ext = await get_extraction_engine().extract_timesheet(
            unit.payload, unit.name, "", "full-email", unit.name)
    except Exception:
        return normalize_sheet(unit, {"kind": "other"})
    buckets = {
        "annual": ext.annual_leave_dates or [], "remote": ext.remote_work_dates or [],
        "sick": ext.sick_leave_dates or [], "maternity": ext.maternity_leave_dates or [],
        "unpaid": ext.unpaid_leave_dates or [],
        "absent": ext.absent_dates or [], "public_holiday": ext.public_holiday_dates or [],
    }
    has_period = bool(1 <= (ext.month or 0) <= 12 and (ext.year or 0) >= 2000)
    has_leave_data = any(buckets.values())
    if unit.name == "(email body)":
        has_data = has_leave_data
    else:
        has_data = has_leave_data or has_period or ext.employee_id or ext.employee_name
    clf = getattr(unit, "classify", None)
    kind = "timesheet" if has_data else "other"
    if clf is not None and getattr(clf, "kind", None) in ("approval", "leave_certificate"):
        kind = clf.kind
    return normalize_sheet(unit, {
        "kind": kind,
        "employee_name": ext.employee_name, "employee_id": ext.employee_id,
        "month": ext.month if has_period else None,
        "year": ext.year if has_period else None,
        **buckets,
    })


def _parse_sheet_reply(parsed) -> dict | None:
    """Accept flat JSON or legacy {\"sheets\": […]} with one entry."""
    if not isinstance(parsed, dict):
        return None
    if "kind" in parsed or any(k in parsed for k in ("annual", "employee_name", "month")):
        return parsed
    entries = parsed.get("sheets")
    if isinstance(entries, list) and entries and isinstance(entries[0], dict):
        return entries[0]
    return None


async def analyse_units(email: EmailMessage, units: list[SheetUnit]) -> tuple[list[dict], dict]:
    """Classify → one vision extract per sheet; engine fallback per sheet."""
    from app.services.extract_email.classify import classify_units
    from app.services.extraction import vision_client
    from app.services.extraction.parser import extract_json_from_llm_response

    provider = vision_client.vision_provider()
    model = vision_client.model_for(provider, "vision")
    api_key = vision_client.openai_api_key()
    use_vision = (settings.extraction_engine == "vision"
                  and bool(api_key) and api_key.lower() != "change-me")
    sheets: list[dict | None] = [None] * len(units)
    calls = 0
    errors: list[str] = []
    classify_meta: list[dict] = []

    clf_results = await classify_units(
        units, subject=getattr(email, "subject", None), use_vision=use_vision)
    classify_meta = [r.as_meta() for r in clf_results]

    if use_vision:
        import asyncio

        from app.services.extract_email.progress import count_llm, emit

        # Each sheet is an independent read, so they run CONCURRENTLY — running
        # them one-after-another simply summed their latencies (a 2-sheet email
        # cost extract(a)+extract(b) instead of max(a, b)). Bounded so a large
        # email can't burst past the provider's rate limit.
        sem = asyncio.Semaphore(EXTRACT_CONCURRENCY)

        async def extract_one(idx: int, u: SheetUnit) -> None:
            nonlocal calls
            native = is_native_file_unit(u, provider)
            prompt = extract_prompt(email, u, native=native)
            images = list(u.images or [])
            if not native and not images and u.payload and u.ftype == "image":
                images = [u.payload]
            # Nothing to send for this sheet — leave it to the engine fallback.
            if not (native and u.payload) and not images and not (u.text or "").strip():
                return
            async with sem:
                try:
                    emit("extract", "spin",
                         f"Reading {u.name} with {model}…", sheets=u.name)
                    # Empty system — full extraction instructions live in `prompt`.
                    if native and u.payload:
                        raw = await vision_client._openai_by_files(
                            [(u.payload, u.ftype)], prompt, "", model, api_key)
                    else:
                        detail = ("low" if (u.text and not images)
                                  else settings.vision_image_detail)
                        raw = await vision_client._openai_by_images(
                            images[:1] if images else [], prompt, "",
                            model, detail, api_key)
                    count_llm()
                    parsed = extract_json_from_llm_response(raw)
                    entry = _parse_sheet_reply(parsed)
                    if entry is None:
                        raise ValueError("model reply was not a sheet JSON object")
                    sheets[idx] = normalize_sheet(u, entry)
                    calls += 1
                    emit("extract", "ok", f"Extracted {u.name}.")
                except Exception as exc:
                    errors.append(f"{u.name}: {str(exc)[:120]}")
                    emit("extract", "warn",
                         f"Read failed for {u.name} — engine fallback ({str(exc)[:80]}).")

        await asyncio.gather(*(extract_one(i, u) for i, u in enumerate(units)))

    for idx, u in enumerate(units):
        if sheets[idx] is None:
            sheets[idx] = await engine_sheet(u)

    method = "vision" if calls else "engine-per-file"
    if calls and errors:
        method = "vision+fallback"
    meta = {
        "method": method,
        "model": model if use_vision and calls else None,
        "classify_model": (settings.openai_classify_model if use_vision else None),
        "calls": calls,
        "sheet_count": len(units),
        "errors": errors[:4],
        "classify": classify_meta,
    }
    boosted = [
        demote_financial_sheet(
            sanitize_body_sheet(
                boost_sheet_from_hints(sheets[i], units[i], email.subject),
                units[i],
            ),
            units[i],
        )
        for i in range(len(units))
        if sheets[i] is not None
    ]
    return boosted, meta
