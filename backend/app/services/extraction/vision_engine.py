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
from app.services.extraction.validation import validate

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
        raw = await vision_client.extract_timesheet(
            images_jpeg=images,
            prompt=parser.EXTRACTION_PROMPT,
            system_prompt=parser.SYSTEM_PROMPT,
            model=model,
            image_detail=settings.vision_image_detail,
            file_bytes=data, file_type=ftype, filename=filename,
        )
        parsed = parser.parse_extraction(parser.extract_json_from_vllm_response(raw))

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

        # optional text cross-validation (mirrors your worker's diff step)
        if settings.enable_text_validation and (settings.openai_api_key or "").strip():
            try:
                flags += await self._text_crosscheck(ftype, data, cleaned, month, year)
            except Exception:
                pass

        flags = list(dict.fromkeys(flags))  # dedupe, keep order
        status = "manual_review" if flags else "verified"
        if flags:
            summary = "Needs review: " + " ".join(flags[:6])
        else:
            total = sum(len(v) for v in cleaned.values())
            mname = calendar.month_name[month] if 1 <= month <= 12 else month
            summary = f"Clean extraction — {total} leave/holiday day(s) for {mname} {year}."

        return TimesheetExtraction(
            employee_id=(parsed.employee_id or None),
            employee_name=(parsed.employee_full_name or None),
            month=month, year=year,
            annual_leave_dates=cleaned["annual"],
            remote_work_dates=cleaned["remote"],
            sick_leave_dates=cleaned["sick"],
            unpaid_leave_dates=cleaned["unpaid"],
            absent_dates=cleaned["absent"],
            public_holiday_dates=cleaned["public_holiday"],
            validation_status=status, summary=summary, hr_flags=flags,
        )

    async def _text_crosscheck(self, ftype, data, cleaned, month, year) -> list[str]:
        doc_text = fp.extract_document_text(ftype, data)
        if not doc_text.strip():
            return []
        prompt = parser.build_text_extraction_prompt(doc_text)
        raw = await vision_client.validate_extraction(
            prompt, system_prompt=parser.TEXT_EXTRACTION_SYSTEM, model=settings.validation_model
        )
        tx = parser.parse_text_extraction(raw)

        def _norm(dates) -> set[str]:
            # Normalise both passes to ISO so "2026-02-17" and "17-Feb-2026"
            # are treated as the same day (avoids false "differ" flags).
            out: set[str] = set()
            for d in dates or []:
                pd = parser._parse_one_leave_date(str(d), month, year)
                out.add(pd.isoformat() if pd else str(d).strip())
            return out

        out: list[str] = []
        pairs = [("Annual", cleaned["annual"], tx.annual_dates),
                 ("Sick", cleaned["sick"], tx.sick_dates),
                 ("Public holiday", cleaned["public_holiday"], tx.public_holiday_dates),
                 ("Unpaid", cleaned["unpaid"], tx.unpaid_dates),
                 ("Absent", cleaned["absent"], tx.absent_dates)]
        for label, vis, txt in pairs:
            nvis, ntxt = _norm(vis), _norm(txt)
            if nvis != ntxt:
                only_vis = sorted(nvis - ntxt)
                only_txt = sorted(ntxt - nvis)
                vis_str = ", ".join(sorted(nvis)) or "none"
                txt_str = ", ".join(sorted(ntxt)) or "none"
                detail = f"validation: {label} differ — vision found [{vis_str}], text found [{txt_str}]."
                if only_vis:
                    detail += f" Only in vision: {', '.join(only_vis)}."
                if only_txt:
                    detail += f" Only in text: {', '.join(only_txt)}."
                out.append(detail)
        return out

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
