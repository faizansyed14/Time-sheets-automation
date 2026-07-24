"""First-pass sheet classification with gpt-4o-mini.

Identifies format_id + kind and whether a daily timesheet covers every day of
the month (dates_complete). Result is stored on the SheetUnit and carried into
normalize_sheet. Deterministic formats.detect_format is the mock/keyless fallback.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

from app.core.config import settings
from app.services.extract_email.approval_prompts import CLASSIFY_SYSTEM, CLASSIFY_USER
from app.services.extract_email.formats import KNOWN_FORMAT_IDS, detect_format, get_format
from app.services.extract_email.types import SheetUnit

# Max classify calls in flight at once. Sheets are independent, so they run
# concurrently; the cap keeps a large email from bursting past the provider's
# rate limit.
CLASSIFY_CONCURRENCY = 4


@dataclass
class ClassifyResult:
    format_id: str = "generic"
    kind: str = "other"
    month: int | None = None
    year: int | None = None
    expected_day_count: int = 0
    observed_day_count: int = 0
    dates_complete: bool = True
    missing_days: list[int] = field(default_factory=list)
    confidence: str = "low"
    source: str = "fallback"  # "llm" | "fallback"

    def as_meta(self) -> dict:
        return {
            "format_id": self.format_id,
            "kind": self.kind,
            "month": self.month,
            "year": self.year,
            "expected_day_count": self.expected_day_count,
            "observed_day_count": self.observed_day_count,
            "dates_complete": self.dates_complete,
            "missing_days": list(self.missing_days),
            "confidence": self.confidence,
            "source": self.source,
        }


def _coerce_kind(v) -> str:
    k = str(v or "other").lower().strip()
    return k if k in ("timesheet", "leave_certificate", "approval", "other") else "other"


def _coerce_format_id(v, kind: str) -> str:
    fid = str(v or "generic").strip()
    if fid in KNOWN_FORMAT_IDS:
        return fid
    if kind == "leave_certificate":
        return "leave_certificate"
    if kind == "approval":
        return "approval"
    return "generic"


def _coerce_month(v) -> int | None:
    try:
        m = int(v)
        return m if 1 <= m <= 12 else None
    except (TypeError, ValueError):
        return None


def _coerce_year(v) -> int | None:
    try:
        y = int(v)
        return y if 2000 <= y <= 2100 else None
    except (TypeError, ValueError):
        return None


def parse_classify_payload(raw: dict) -> ClassifyResult:
    """Normalize model/fallback JSON into ClassifyResult."""
    kind = _coerce_kind(raw.get("kind"))
    format_id = _coerce_format_id(raw.get("format_id"), kind)
    month = _coerce_month(raw.get("month"))
    year = _coerce_year(raw.get("year"))
    try:
        expected = max(0, int(raw.get("expected_day_count") or 0))
    except (TypeError, ValueError):
        expected = 0
    try:
        observed = max(0, int(raw.get("observed_day_count") or 0))
    except (TypeError, ValueError):
        observed = 0
    missing: list[int] = []
    for d in raw.get("missing_days") or []:
        try:
            n = int(d)
            if 1 <= n <= 31:
                missing.append(n)
        except (TypeError, ValueError):
            pass
    missing = sorted(set(missing))
    if expected and month and year and not missing and observed:
        # Recompute completeness if model omitted missing_days.
        last = calendar.monthrange(year, month)[1]
        if expected != last:
            expected = last
    dates_complete = bool(raw.get("dates_complete", True))
    if kind == "timesheet" and expected > 0:
        if missing:
            dates_complete = False
        elif observed < expected:
            dates_complete = False
    conf = str(raw.get("confidence") or "low").lower()
    if conf not in ("high", "medium", "low"):
        conf = "low"
    return ClassifyResult(
        format_id=format_id,
        kind=kind,
        month=month,
        year=year,
        expected_day_count=expected,
        observed_day_count=observed,
        dates_complete=dates_complete,
        missing_days=missing,
        confidence=conf,
        source=str(raw.get("source") or "llm"),
    )


def fallback_classify(unit: SheetUnit, subject: str | None = None) -> ClassifyResult:
    """Deterministic classify when vision/API is unavailable."""
    spec = detect_format(unit.text or "", unit.name or "", subject or "")
    kind = "other"
    if spec.id == "leave_certificate":
        kind = "leave_certificate"
    elif spec.id == "approval":
        kind = "approval"
    elif unit.name == "(email body)":
        kind = "other"
    elif unit.ftype in ("pdf", "docx", "xlsx", "image") and (unit.text or unit.images):
        # Assume attachment docs are timesheets until proven otherwise.
        kind = "timesheet" if spec.id not in ("generic",) or bool(unit.text) else "other"
        if spec.id == "generic" and not unit.text and unit.images:
            kind = "other"  # scan — unknown without LLM
        elif spec.id != "generic":
            kind = "timesheet"
    return ClassifyResult(
        format_id=spec.id,
        kind=kind,
        dates_complete=True,
        confidence="low",
        source="fallback",
    )


def apply_classify_to_unit(unit: SheetUnit, result: ClassifyResult) -> None:
    unit.format_id = result.format_id
    unit.classify = result


async def classify_unit(
    unit: SheetUnit,
    *,
    subject: str | None = None,
    use_vision: bool = True,
) -> ClassifyResult:
    """Classify one sheet. Prefers native file / image to gpt-4o-mini."""
    from app.services.extract_email.progress import count_llm, emit

    if unit.name == "(email body)" and not unit.images:
        result = ClassifyResult(
            format_id="generic", kind="other", dates_complete=True,
            confidence="high", source="fallback")
        apply_classify_to_unit(unit, result)
        return result

    if not use_vision:
        result = fallback_classify(unit, subject)
        apply_classify_to_unit(unit, result)
        return result

    from app.services.extraction import vision_client
    from app.services.extraction.parser import extract_json_from_llm_response

    api_key = vision_client.openai_api_key()
    if not api_key or api_key.lower() == "change-me":
        result = fallback_classify(unit, subject)
        apply_classify_to_unit(unit, result)
        return result

    model = (settings.openai_classify_model or "gpt-4o-mini").strip()
    emit("format", "spin", f"Classifying {unit.name} with {model}…", sheet=unit.name)
    try:
        provider = vision_client.vision_provider()
        native = (provider == "openai" and unit.ftype in vision_client.NATIVE_FILE_TYPES
                  and unit.payload)
        if native:
            raw = await vision_client._openai_by_files(
                [(unit.payload, unit.ftype)], CLASSIFY_USER, CLASSIFY_SYSTEM, model, api_key)
        else:
            images = list(unit.images or [])
            if not images and unit.payload and unit.ftype == "image":
                images = [unit.payload]
            if not images and unit.text:
                # Text-only grounding for classify when no image (rare).
                prompt = (CLASSIFY_USER + f'\n\nFILENAME: {unit.name}\n'
                          f"EXACT TEXT (truncated):\n{(unit.text or '')[:6000]}")
                raw = await vision_client._openai_by_images(
                    [], prompt, CLASSIFY_SYSTEM, model, "low", api_key)
            elif not images:
                result = fallback_classify(unit, subject)
                apply_classify_to_unit(unit, result)
                emit("format", "warn", f"No image/file for {unit.name} — used marker fallback.")
                return result
            else:
                raw = await vision_client._openai_by_images(
                    images[:1], CLASSIFY_USER, CLASSIFY_SYSTEM, model,
                    settings.vision_image_detail, api_key)
        count_llm()
        parsed = extract_json_from_llm_response(raw)
        if not isinstance(parsed, dict):
            raise ValueError("classify reply was not a JSON object")
        result = parse_classify_payload(parsed)
        result.source = "llm"
        apply_classify_to_unit(unit, result)
        label = get_format(result.format_id).label
        status = "ok" if result.dates_complete else "warn"
        msg = f"{unit.name} → {label} ({result.kind})"
        if not result.dates_complete:
            msg += f" — incomplete ({result.observed_day_count}/{result.expected_day_count} days)"
        emit("format", status, msg, **result.as_meta())
        return result
    except Exception as exc:
        emit("format", "warn",
             f"Classify failed for {unit.name} — marker fallback ({str(exc)[:80]}).")
        result = fallback_classify(unit, subject)
        apply_classify_to_unit(unit, result)
        return result


async def classify_units(
    units: list[SheetUnit],
    *,
    subject: str | None = None,
    use_vision: bool = True,
) -> list[ClassifyResult]:
    """Classify every sheet CONCURRENTLY — each sheet is independent, so running
    them one-after-another just added their latencies together. Bounded by a
    semaphore so a 12-sheet email can't burst past the provider's rate limit."""
    import asyncio

    sem = asyncio.Semaphore(CLASSIFY_CONCURRENCY)

    async def one(u: SheetUnit) -> ClassifyResult:
        async with sem:
            return await classify_unit(u, subject=subject, use_vision=use_vision)

    return list(await asyncio.gather(*(one(u) for u in units)))
