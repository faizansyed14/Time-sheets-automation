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
from app.core.openai_url import openai_urls
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


def _provider_key(provider: str) -> str:
    """The API key configured for a provider (to gate optional AI steps)."""
    _, _, key, _, _ = vision_client._chat_endpoint(provider)
    return key or ""


def _dates(occs) -> list[str]:
    return [o.date.isoformat() for o in occs if (o.day_of_week not in _WEEKEND)]


# Question asked about approval-screenshot attachments during email ingest.
# Module-level so the admin UI can display it in the prompt inventory.
APPROVAL_QUESTION = ('Does this screenshot show a manager APPROVING leave? '
                     'Return ONLY JSON: {"approved": true/false, "detail": "<who/when, short>"}')


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
                hr_flags=["Unsupported file type."],
                extraction_method="unsupported")

        model = vision_client.model_for(vision_client.vision_provider(), "vision")
        # Authoritative cell/text content (for .eml this reads the embedded
        # attachment) — handed to the model so a poor image render can't make
        # it hallucinate names/IDs/dates.
        doc_text = fp.extract_document_text(ftype, data)
        has_text = len((doc_text or "").strip()) >= 24
        # Scans/photos/screenshots have no digital text layer, so the read has
        # no independent grounding (OCR at best) — remembered here, flagged
        # below so such sheets are never filed as "verified" unseen.
        born_digital = has_text

        # The bytes/type/name the LLM should treat as THE document. For an .eml
        # carrying a real PDF/Office sheet ("email inside the email"), point the
        # model at that attachment directly — far more reliable and cheaper than
        # rendering the whole email to many images.
        llm_bytes, llm_ftype, llm_name = data, ftype, filename
        embedded_meta: dict = {}
        if ftype == "eml":
            best = fp.eml_best_attachment(data)
            if best:
                emb_name, emb_payload, emb_ftype = best
                llm_bytes, llm_ftype, llm_name = emb_payload, emb_ftype, (emb_name or filename)
                embedded_meta = {"embedded_attachment": emb_name, "embedded_type": emb_ftype}

        # Render: digital PDFs are grounded by their text layer, so a low DPI is
        # plenty (cheaper); scanned PDFs (no text layer) need more resolution.
        render_dpi: int | None = None
        if llm_ftype == "pdf":
            base_dpi = int(getattr(settings, "pdf_render_dpi", 150) or 150)
            render_dpi = base_dpi if has_text else max(base_dpi, 220)
            images = fp.pdf_to_images(llm_bytes, dpi=render_dpi)
        else:
            images = fp.to_images(llm_ftype, llm_bytes)

        # OCR reader (optional): give scans/photos a text layer so they get the
        # same grounding as digital sheets. No-op unless OCR_PROVIDER is set.
        used_ocr = False
        ocr_provider = (settings.ocr_provider or "none").strip().lower()
        ocr_status = "disabled"
        if not has_text and ocr_provider != "none":
            from app.services.extraction import ocr
            ocr_status = ocr.ocr_status()  # ready | not_installed | unknown_provider
            if ocr_status == "ready":
                try:
                    ocr_txt = ocr.ocr_text(images, llm_bytes, llm_ftype)
                    if ocr_txt.strip():
                        doc_text, has_text = ocr_txt, len(ocr_txt.strip()) >= 24
                        used_ocr = True
                except Exception:
                    ocr_status = "error"

        # Build the provenance shown in the tracker's "Extraction details" panel.
        meta = {
            "file_type": ftype,
            "page_count": len(images),
            "render_dpi": render_dpi,
            "has_text_layer": bool(has_text),
            "doc_text_chars": len((doc_text or "").strip()),
            "ocr_provider": ocr_provider,
            "ocr_status": ocr_status,
            "validation_model": (vision_client.model_for(vision_client.validation_provider(), "validation")
                                 if settings.enable_text_validation else None),
            **embedded_meta,
        }

        # Deterministic-first (opt-in): a clean digital text layer that parses
        # confidently skips the LLM entirely — the biggest cost saver.
        if settings.extraction_prefer_deterministic and has_text:
            det = self._deterministic_from_text(doc_text)
            if det is not None:
                det.used_ocr = used_ocr
                det.extraction_meta = {**meta, "image_detail": None}
                return det

        # Adaptive image detail: born-digital sheets are grounded by their text
        # layer, so the image only needs LOW detail (≈5–10× cheaper); scans keep
        # the configured (high) detail.
        detail = "low" if (settings.vision_adaptive_detail and has_text) else settings.vision_image_detail
        meta["image_detail"] = detail
        raw = await vision_client.extract_timesheet(
            images_jpeg=images,
            prompt=parser.get_prompt("extraction"),
            system_prompt=parser.get_prompt("system"),
            model=model,
            image_detail=detail,
            file_bytes=llm_bytes, file_type=llm_ftype, filename=llm_name,
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
            "maternity": _dates(parsed.maternity_leave_dates),
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
        if settings.enable_text_validation and _provider_key(vision_client.validation_provider()).strip():
            try:
                period_flag, date_flags = await self._text_crosscheck(
                    doc_text, cleaned, month, year)
                # A period disagreement is the single most important signal that
                # the main read is wrong (e.g. it said Jan 2023 but the dates are
                # May 2026); surface it first and clearly.
                flags = ([period_flag] if period_flag else []) + flags + date_flags
            except Exception:
                pass

        if not born_digital and sum(len(v) for v in cleaned.values()) > 0:
            flags = flags + ["Image-only sheet (scan/photo/screenshot) — auto-read "
                             "can miss merged or colour-coded cells; please "
                             "double-check the dates before approving."]

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
            maternity_leave_dates=cleaned["maternity"],
            unpaid_leave_dates=cleaned["unpaid"],
            absent_dates=cleaned["absent"],
            public_holiday_dates=cleaned["public_holiday"],
            validation_status=status, summary=summary, hr_flags=flags,
            extraction_model=model, extraction_method="vision-llm", used_ocr=used_ocr,
            extraction_meta=meta,
        )

    def _deterministic_from_text(self, doc_text: str) -> TimesheetExtraction | None:
        """Extract WITHOUT an LLM from a clean text layer. Returns a result only
        when identity (id + name) and period are confidently present; otherwise
        None so the caller falls back to the vision model."""
        from app.services.extraction.mock_engine import _parse_upload_text
        from app.services.pipeline.matching import _is_placeholder_name

        parsed = _parse_upload_text(doc_text)
        if not parsed:
            return None
        emp_id = (parsed.get("emp_id") or "").strip()
        emp_name = (parsed.get("emp_name") or "").strip()
        month = parsed.get("month") or 0
        year = parsed.get("year") or 0
        if not emp_id or not emp_name or _is_placeholder_name(emp_name):
            return None
        if not (1 <= month <= 12 and year >= 2000):
            return None

        raw = {b: parsed.get(b, []) for b in
               ("annual", "remote", "sick", "maternity", "unpaid", "absent", "public_holiday")}
        cleaned, flags = validate(raw, month, year)
        try:
            present = set(parsed.get("_present") or [])
            if len(present) >= 5:
                accounted = {d for v in cleaned.values() for d in v}
                gaps = unaccounted_working_days(
                    month, year, present, set(parsed.get("_weekend") or []), accounted)
                uf = unaccounted_flag(gaps)
                if uf:
                    flags = flags + [uf]
        except Exception:
            pass
        status = "manual_review" if flags else "verified"
        total = sum(len(v) for v in cleaned.values())
        mname = calendar.month_name[month]
        summary = ("Needs review: " + " ".join(flags[:4])) if flags else (
            f"Clean extraction (no-LLM, digital text) — {total} leave/holiday day(s) for {mname} {year}.")
        return TimesheetExtraction(
            employee_id=emp_id, employee_name=emp_name, month=month, year=year,
            annual_leave_dates=cleaned["annual"], remote_work_dates=cleaned["remote"],
            sick_leave_dates=cleaned["sick"], maternity_leave_dates=cleaned["maternity"],
            unpaid_leave_dates=cleaned["unpaid"],
            absent_dates=cleaned["absent"], public_holiday_dates=cleaned["public_holiday"],
            validation_status=status, summary=summary, hr_flags=flags,
            extraction_model=None, extraction_method="deterministic-text",
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
            prompt, system_prompt=parser.TEXT_EXTRACTION_SYSTEM,
            model=vision_client.model_for(vision_client.validation_provider(), "validation"),
        )
        tx = parser.parse_text_extraction(raw)

        import calendar as _cal
        from collections import Counter

        # ---- period sanity: where do the text-read dates actually fall? ----
        text_dates = []
        for lst in (tx.annual_dates, tx.sick_dates, tx.maternity_dates, tx.public_holiday_dates,
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
                 ("maternity leave", cleaned["maternity"], tx.maternity_dates),
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
        if not _provider_key(vision_client.validation_provider()).strip():
            return None
        try:
            prompt = parser.build_summary_prompt(context)
            raw = await vision_client.validate_extraction(
                prompt, system_prompt=parser.SUMMARY_SYSTEM,
                model=vision_client.model_for(vision_client.validation_provider(), "validation"))
            text = parser._collect_text(raw).strip()
            text = text.replace("```", "").strip()
            return text or None
        except Exception:
            return None

    async def extract_approval(
        self, data: bytes, message_id: str, attachment_id: str,
    ) -> ApprovalExtraction:
        question = APPROVAL_QUESTION
        provider = vision_client.vision_provider()
        model = vision_client.model_for(provider, "vision")
        try:
            if provider == "openai":
                api_key = (settings.openai_api_key or "").strip()
                if not api_key:
                    return ApprovalExtraction(detected=False, detail="No OpenAI key for approval reading.")
                b64 = base64.b64encode(data).decode("utf-8")
                api_root, _ = openai_urls(settings.openai_base_url)
                payload = {
                    "model": model if not model.startswith("gpt-5") else "gpt-4o",
                    "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}},
                        {"type": "text", "text": question},
                    ]}],
                    "max_tokens": 200, "temperature": 0.0,
                }
                async with httpx.AsyncClient(timeout=httpx.Timeout(settings.openai_timeout)) as client:
                    r = await client.post(f"{api_root}/v1/chat/completions", json=payload,
                                          headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
                    r.raise_for_status()
                    raw = r.json()
            else:
                if not vision_client._chat_endpoint(provider)[2]:
                    return ApprovalExtraction(detected=False, detail=f"No {provider} key for approval reading.")
                raw = await vision_client._chat_compatible(provider, [data], question, None, model, "low")
            import json
            txt = raw["choices"][0]["message"]["content"].replace("```json", "").replace("```", "").strip()
            obj = json.loads(txt)
            return ApprovalExtraction(detected=bool(obj.get("approved")), detail=str(obj.get("detail") or ""))
        except Exception as e:
            return ApprovalExtraction(detected=False, detail=f"Could not read approval ({str(e)[:80]}).")
