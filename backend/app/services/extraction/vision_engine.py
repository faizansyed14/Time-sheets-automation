"""
Real extraction engine — uses YOUR prompts + vision client + file conversion.

Flow per timesheet:
  detect type -> render images -> vision LLM (SYSTEM_PROMPT + EXTRACTION_PROMPT)
  -> parse JSON -> canonical buckets -> deterministic validation (+ optional
  text cross-check with gpt-4o-mini) -> verified/manual_review + summary.

Approval screenshot: a small vision call returns {approved, detail}.

Select with EXTRACTION_ENGINE=vision and set OPENAI_API_KEY (and/or VLLM_*).
"""
from __future__ import annotations

import base64
import calendar

import httpx

from app.core.config import settings
from app.services.extraction import file_processor as fp
from app.services.extraction import parser, vision_client
from app.services.extraction.base import (
    ApprovalExtraction,
    ExtractionEngine,
    TimesheetExtraction,
)
from app.services.extraction.validation import (
    unaccounted_flag,
    unaccounted_working_days,
    validate,
)

_WEEKEND = {"Saturday", "Sunday"}


def _dates(occs) -> list[str]:
    return [o.date.isoformat() for o in occs if (o.day_of_week not in _WEEKEND)]


class VisionExtractionEngine(ExtractionEngine):
    async def extract_timesheet(
        self, data: bytes, filename: str, content_type: str,
        message_id: str, attachment_id: str,
    ) -> TimesheetExtraction:
        ftype = fp.detect_file_type(filename, data)
        if ftype == "unknown":
            return TimesheetExtraction(
                employee_id=None, employee_name=None, month=0, year=0,
                validation_status="manual_review", summary="Unsupported file type.",
                hr_flags=["Unsupported file type."])

        images = fp.to_images(ftype, data)
        model = settings.extraction_model
        # Authoritative cell/text content (for .eml this reads the embedded
        # attachment) — handed to the model so a poor image render can't make
        # it hallucinate names/IDs/dates.
        doc_text = fp.extract_document_text(ftype, data)
        raw = await vision_client.extract_timesheet(
            images_jpeg=images,
            prompt=parser.get_prompt("extraction"),
            system_prompt=parser.get_prompt("system"),
            model=model,
            image_detail=settings.vision_image_detail,
            file_bytes=data, file_type=ftype, filename=filename,
            aux_text=doc_text,
        )
        parsed = parser.parse_extraction(parser.extract_json_from_vllm_response(raw))

        # Drop hallucinated placeholder identities so they never reach matching.
        from app.services.pipeline.matching import _is_placeholder_name
        emp_name = parsed.employee_full_name
        if emp_name and _is_placeholder_name(emp_name):
            emp_name = None

        month = parsed.month or 0
        year = parsed.year or 0

        # canonical buckets (annual = annual + paid, per your flow)
        buckets = {
            "annual": sorted(set(_dates(parsed.annual_leave_dates) + _dates(parsed.paid_leave_dates))),
            "remote": _dates(parsed.work_from_home_dates),
            "sick": _dates(parsed.sick_leave_dates),
            "unpaid": _dates(parsed.unpaid_leave_dates),
            "absent": _dates(parsed.absent_dates),
            "public_holiday": [o.date.isoformat() for o in parsed.public_holidays_dates],
        }
        cleaned, flags = validate(buckets, month, year)

        # Daily-grid sheets: flag weekdays with neither hours nor a leave entry
        # (genuine gaps a reviewer must confirm). Uses the same document text.
        try:
            present, weekend = fp.scan_attendance_grid(doc_text)
            if len(present) >= 5:
                accounted = {d for v in cleaned.values() for d in v}
                gaps = unaccounted_working_days(month, year, present, weekend, accounted)
                uf = unaccounted_flag(gaps)
                if uf:
                    flags = flags + [uf]
        except Exception:
            pass

        # optional text cross-validation (mirrors your worker's diff step)
        if settings.enable_text_validation and (settings.openai_api_key or "").strip():
            try:
                period_flag, date_flags = await self._text_crosscheck(
                    doc_text, cleaned, month, year)
                # A period disagreement is the single most important signal that
                # the main read is wrong (e.g. it said Jan 2023 but the dates are
                # May 2026); surface it first and clearly.
                flags = ([period_flag] if period_flag else []) + flags + date_flags
            except Exception:
                pass

        flags = list(dict.fromkeys(flags))  # dedupe, keep order
        status = "manual_review" if flags else "verified"
        total = sum(len(v) for v in cleaned.values())
        mname = calendar.month_name[month] if 1 <= month <= 12 else month
        if flags:
            summary = "Needs review: " + " ".join(flags[:4])
        else:
            summary = f"Clean extraction — {total} leave/holiday day(s) for {mname} {year}."

        return TimesheetExtraction(
            employee_id=(parsed.employee_id or None),
            employee_name=(emp_name or None),
            month=month, year=year,
            annual_leave_dates=cleaned["annual"],
            remote_work_dates=cleaned["remote"],
            sick_leave_dates=cleaned["sick"],
            unpaid_leave_dates=cleaned["unpaid"],
            absent_dates=cleaned["absent"],
            public_holiday_dates=cleaned["public_holiday"],
            validation_status=status, summary=summary, hr_flags=flags,
        )

    async def _text_crosscheck(self, doc_text, cleaned, month, year) -> tuple[str | None, list[str]]:
        """Compare the main read against an independent text read of the same
        file. Returns (period_flag, date_flags):
          - period_flag: set when the two reads disagree on the MONTH/YEAR (the
            strongest sign the main read is wrong). In that case the per-date
            diffs are just noise, so they are suppressed and only this one clear
            flag is returned.
          - date_flags: concise per-category differences when the period agrees.
        """
        if not (doc_text or "").strip():
            return None, []
        prompt = parser.build_text_extraction_prompt(doc_text)
        raw = await vision_client.validate_extraction(
            prompt, system_prompt=parser.TEXT_EXTRACTION_SYSTEM, model=settings.validation_model
        )
        tx = parser.parse_text_extraction(raw)

        import calendar as _cal
        from collections import Counter

        # ---- period sanity: where do the text-read dates actually fall? ----
        text_dates = []
        for lst in (tx.annual_dates, tx.sick_dates, tx.public_holiday_dates,
                    tx.unpaid_dates, tx.absent_dates, tx.work_from_home_dates):
            for d in lst or []:
                pd = parser._parse_one_leave_date(str(d), None, None)
                if pd:
                    text_dates.append(pd)
        if text_dates:
            (ty, tm), cnt = Counter((d.year, d.month) for d in text_dates).most_common(1)[0]
            if (tm, ty) != (month, year) and cnt >= max(2, len(text_dates) // 2):
                vis_p = f"{_cal.month_name[month]} {year}" if (1 <= month <= 12 and year) else "an unclear period"
                period_flag = (
                    f"Likely wrong period — the main read recorded this as {vis_p}, but the file's "
                    f"own text shows the dates fall in {_cal.month_name[tm]} {ty}. Please confirm the "
                    f"correct month/year before approving."
                )
                return period_flag, []

        # ---- same period: concise per-category date differences ----
        def _norm(dates) -> set[str]:
            out: set[str] = set()
            for d in dates or []:
                pd = parser._parse_one_leave_date(str(d), month, year)
                out.add(pd.isoformat() if pd else str(d).strip())
            return out

        out: list[str] = []
        pairs = [("annual leave", cleaned["annual"], tx.annual_dates),
                 ("sick leave", cleaned["sick"], tx.sick_dates),
                 ("public holiday", cleaned["public_holiday"], tx.public_holiday_dates),
                 ("unpaid leave", cleaned["unpaid"], tx.unpaid_dates),
                 ("absent", cleaned["absent"], tx.absent_dates)]
        for label, vis, txt in pairs:
            nvis, ntxt = _norm(vis), _norm(txt)
            if nvis == ntxt:
                continue
            out.append(self._crosscheck_flag(label, sorted(nvis - ntxt), sorted(ntxt - nvis)))
        return None, out

    @staticmethod
    def _examples(dates: list[str], n: int = 3) -> str:
        """Format up to n ISO dates as '06 May, 08 May, 09 May …'."""
        import datetime as _dt
        shown = []
        for d in dates[:n]:
            try:
                shown.append(_dt.date.fromisoformat(d).strftime("%d %b"))
            except Exception:
                shown.append(d)
        more = " …" if len(dates) > n else ""
        return ", ".join(shown) + more

    def _crosscheck_flag(self, label: str, only_vis: list[str], only_txt: list[str]) -> str:
        """A concise, readable cross-check flag — counts + a few example dates,
        never a full dump of every date."""
        if only_txt and not only_vis:
            return (f"Cross-check: a second read of the file flagged "
                    f"{len(only_txt)} possible {label} day(s) the main read missed "
                    f"({self._examples(only_txt)}). Please confirm before approving.")
        if only_vis and not only_txt:
            return (f"Cross-check: {len(only_vis)} {label} day(s) from the main read "
                    f"were not confirmed by the second read "
                    f"({self._examples(only_vis)}). Please verify.")
        return (f"Cross-check: the two reads of this file disagree on {label} — "
                f"{len(only_vis)} only in the main read ({self._examples(only_vis)}) and "
                f"{len(only_txt)} only in the second read ({self._examples(only_txt)}). "
                f"Please confirm the correct dates.")

    async def summarize(self, context: dict) -> str | None:
        """Polished plain-English summary via the SUMMARY prompt (vision mode)."""
        if not (settings.openai_api_key or "").strip():
            return None
        try:
            prompt = parser.build_summary_prompt(context)
            raw = await vision_client.validate_extraction(
                prompt, system_prompt=parser.SUMMARY_SYSTEM, model=settings.validation_model)
            text = parser._collect_text(raw).strip()
            text = text.replace("```", "").strip()
            return text or None
        except Exception:
            return None

    async def extract_approval(
        self, data: bytes, message_id: str, attachment_id: str,
    ) -> ApprovalExtraction:
        api_key = (settings.openai_api_key or "").strip()
        if not api_key:
            return ApprovalExtraction(detected=False, detail="No OpenAI key for approval reading.")
        try:
            b64 = base64.b64encode(data).decode("utf-8")
            base_url = str(settings.openai_base_url).rstrip("/")
            payload = {
                "model": settings.extraction_model if not settings.extraction_model.startswith("gpt-5") else "gpt-4o",
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}},
                    {"type": "text", "text": 'Does this screenshot show a manager APPROVING leave? '
                     'Return ONLY JSON: {"approved": true/false, "detail": "<who/when, short>"}'},
                ]}],
                "max_tokens": 200, "temperature": 0.0,
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
                r = await client.post(f"{base_url}/v1/chat/completions", json=payload,
                                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
                r.raise_for_status()
                import json
                txt = r.json()["choices"][0]["message"]["content"].replace("```json", "").replace("```", "").strip()
                obj = json.loads(txt)
                return ApprovalExtraction(detected=bool(obj.get("approved")), detail=str(obj.get("detail") or ""))
        except Exception as e:
            return ApprovalExtraction(detected=False, detail=f"Could not read approval ({str(e)[:80]}).")
