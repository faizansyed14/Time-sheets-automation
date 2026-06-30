"""
Inbox AI check — one cheap-model pass over the full email.

Reads subject/body + every attachment (PDF/DOCX/XLSX/EML, including nested
sheets inside .eml). Classifies timesheet / approval / other, recommends what
to extract, and resolves the employee from sender email + sheet text.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.employee import Employee
from app.models.email_message import EmailMessage
from app.services.email_provider import get_email_provider
from app.services.extraction import ocr
from app.services.extraction.file_processor import (
    detect_file_type,
    eml_all_attachments,
    extract_document_text,
    image_to_images,
    to_images,
)
from app.services.inbox.employee_match import match_sender
from app.services.pipeline import matching as employee_matching

_VALID = {"timesheet", "approval", "other"}
_TEXT_CAP = 4_500
_BODY_CAP = 12_000  # full email body for LLM (inline timesheet grids can be long)
_MIN_TEXT = 24


def _is_doc(filename: str, content_type: str) -> bool:
    n = (filename or "").lower()
    c = (content_type or "").lower()
    if c.startswith("image/"):
        return False
    return (
        n.endswith((".pdf", ".docx", ".xlsx", ".doc", ".xls", ".eml"))
        or c in {
            "application/pdf",
            "message/rfc822",
            "application/eml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    )


def _ocr_document_text(ftype: str, data: bytes) -> str:
    """OCR scan/image PDFs — same fallback path as pipeline extraction."""
    if (settings.ocr_provider or "none").strip().lower() in ("", "none"):
        return ""
    if ocr.ocr_status() != "ready":
        return ""
    try:
        images = to_images(ftype, data) if ftype != "image" else image_to_images(data)
        return ocr.ocr_text(images, data, ftype)
    except Exception:
        return ""


def _enrich_doc_text(ftype: str, data: bytes, text: str) -> tuple[str, str, bool]:
    """Return (text, source_kind, used_ocr). OCR when the text layer is thin."""
    plain = (text or "").strip()
    if len(plain) >= _MIN_TEXT:
        return plain, "document", False
    ocr_txt = _ocr_document_text(ftype, data)
    ocr_plain = ocr_txt.strip()
    if len(ocr_plain) >= _MIN_TEXT:
        return ocr_plain, "ocr", True
    if plain:
        return plain, "document", False
    if ocr_plain:
        return ocr_plain, "ocr", True
    return "", "document", False


async def _attachment_text(
    filename: str, content_type: str, data: bytes,
) -> tuple[str, str, list[dict], bool]:
    nested: list[dict] = []
    ftype = detect_file_type(filename, data)
    if ftype == "eml":
        parts: list[str] = []
        for emb_name, emb_payload, emb_ftype in eml_all_attachments(data):
            emb_raw = extract_document_text(emb_ftype, emb_payload)
            emb_text, _, emb_ocr = _enrich_doc_text(emb_ftype, emb_payload, emb_raw)
            nested.append({
                "filename": emb_name,
                "file_type": emb_ftype,
                "text_chars": len((emb_text or "").strip()),
                "used_ocr": emb_ocr,
            })
            if emb_text.strip():
                parts.append(f"--- embedded in EML: {emb_name} ---\n{emb_text}")
        outer = extract_document_text("eml", data)
        if outer.strip():
            parts.insert(0, outer)
        return "\n\n".join(parts).strip(), "eml", nested, False
    if _is_doc(filename, content_type) or ftype in ("pdf", "docx", "xlsx"):
        raw = extract_document_text(ftype, data)
        text, kind, used_ocr = _enrich_doc_text(ftype, data, raw)
        return text, kind, nested, used_ocr
    ocr_txt = _ocr_document_text("image", data)
    return ocr_txt, "image", nested, bool(ocr_txt.strip())


def _coded_category(text: str) -> tuple[str, str]:
    """No-LLM fallback when the model is unavailable."""
    from app.services.inbox.timesheet_detect import coded_category
    return coded_category(text)


async def _llm_classify(
    email: EmailMessage,
    blocks: list[dict[str, Any]],
) -> dict | None:
    api_key = (settings.openai_api_key or "").strip()
    if not api_key or api_key.lower() == "change-me":
        return None

    model = (settings.ai_check_model or "gpt-4o-mini").strip()
    from app.services.inbox.timesheet_detect import plain_email_body

    body_for_llm = plain_email_body(
        subject=email.subject, body_text=email.body_text, body_html=email.body_html)
    lines = [
        f"SUBJECT: {email.subject or ''}",
        f"FROM: {email.sender_name or ''} <{email.sender_email or ''}>",
        f"BODY:\n{body_for_llm[:_BODY_CAP]}",
        "",
        "ATTACHMENTS:",
    ]
    for i, b in enumerate(blocks, 1):
        txt = (b.get("text") or "").strip()
        if len(txt) > _TEXT_CAP:
            txt = txt[:_TEXT_CAP] + "\n[... truncated ...]"
        nested = b.get("nested") or []
        nested_note = ""
        if nested:
            nested_note = " (EML contains: " + ", ".join(
                n.get("filename", "?") for n in nested) + ")"
        fn_hint = b.get("filename_hint") or ""
        ocr_note = " [OCR text]" if b.get("used_ocr") else ""
        lines.append(
            f"{i}. id={b['attachment_id']} file={b['filename']}{nested_note}{ocr_note}\n"
            f"filename_hint: {fn_hint or 'none'}\n"
            f"{txt or '(no readable text — rely on filename_hint if attendance/timesheet)'}"
        )

    system = (
        "You triage HR inbox emails for a timesheet portal. Classify each attachment and the body.\n"
        "Categories:\n"
        "- timesheet: employee attendance/hours/leave by date (including Adobe Sign SIGNED attendance PDFs)\n"
        "- approval: manager approving a timesheet (short note or approval screenshot text)\n"
        "- other: invoices, logos, newsletters, Adobe Sign audit-trail PDFs (filename contains 'audit' "
        "without 'signed'), certificates with no attendance grid\n\n"
        "Rules:\n"
        "- Filenames with ATTENDANCE/TIMESHEET/TIME_SHEET are almost always timesheet unless they are "
        "clearly an Adobe audit trail (…audit.pdf, not signed).\n"
        "- Prefer the signed attendance PDF for recommended_timesheet_ids, not the audit copy.\n"
        "- Body text from Adobe Sign notification emails is NOT a timesheet.\n"
        "- Short emails that only ask someone to approve a timesheet (no pasted grid) are NOT "
        "timesheets — body_category=other, extract_body=false.\n\n"
        "extract_body rules (your decision from the full BODY text):\n"
        "- extract_body=true: the attendance table itself is pasted in the email body (rows of "
        "dates with hours, leave, IN/OUT, EMP NO, MONTH/YEAR) AND no attachment is a timesheet.\n"
        "- extract_body=false: timesheet is only in an attachment, or the body is a short note, "
        "forward, approval request, or merely mentions timesheets/dates without a grid.\n\n"
        "Return ONLY JSON:\n"
        "{\n"
        '  "attachments": [{"attachment_id": "<id>", "category": "timesheet|approval|other", "reason": "<short>"}],\n'
        '  "body_category": "timesheet|approval|other",\n'
        '  "body_reason": "<short>",\n'
        '  "recommended_timesheet_ids": ["<attachment_id>", ...],\n'
        '  "recommended_approval_id": "<attachment_id or null>",\n'
        '  "extract_body": false,\n'
        '  "employee_name_on_sheet": "<name from timesheet text or null>",\n'
        '  "employee_id_on_sheet": "<id from timesheet text or null>"\n'
        "}\n"
    )
    try:
        from app.services.extraction import vision_client
        raw = await vision_client.validate_extraction(
            "\n".join(lines), system_prompt=system, model=model)
        text = _collect_text(raw).strip()
        text = text.replace("```json", "").replace("```", "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def _reconcile_classification(
    blocks: list[dict[str, Any]],
    llm: dict | None,
) -> tuple[list[dict], list[str], str | None, str | None, str | None]:
    """Apply filename/text heuristics after LLM (or when LLM absent)."""
    from app.services.inbox.timesheet_detect import (
        coded_category,
        extract_id_from_filename,
        filename_timesheet_hint,
    )

    llm_atts = {x.get("attachment_id"): x for x in ((llm or {}).get("attachments") or [])}
    by_id = {b["attachment_id"]: b for b in blocks}
    att_out: list[dict] = []
    timesheet_ids: list[str] = []
    approval_id: str | None = None
    sheet_name: str | None = None
    sheet_id: str | None = None

    if llm:
        sheet_name = (llm.get("employee_name_on_sheet") or "").strip() or None
        sheet_id = (llm.get("employee_id_on_sheet") or "").strip() or None

    for b in blocks:
        aid = b["attachment_id"]
        la = llm_atts.get(aid) or {}
        if llm:
            cat = str(la.get("category", "other")).strip().lower()
            if cat not in _VALID:
                cat = "other"
            reason = str(la.get("reason") or "model classification").strip()
            used_llm = True
        else:
            cat, reason = coded_category(b.get("text") or "")
            used_llm = False

        fn_cat, fn_reason = filename_timesheet_hint(b.get("filename") or "")
        if fn_cat:
            if fn_cat == "timesheet" and cat != "timesheet":
                cat, reason = fn_cat, fn_reason
            elif fn_cat == "other":
                cat, reason = fn_cat, fn_reason

        att_out.append({
            "attachment_id": aid,
            "filename": b["filename"],
            "content_type": b.get("content_type", ""),
            "category": cat,
            "reason": reason,
            "source_kind": b.get("source_kind", ""),
            "nested": b.get("nested") or [],
            "used_llm": used_llm,
            "used_ocr": bool(b.get("used_ocr")),
            "text_chars": int(b.get("text_chars") or 0),
        })
        if cat == "timesheet":
            timesheet_ids.append(aid)
        elif cat == "approval" and approval_id is None:
            approval_id = aid

        if not sheet_id:
            sheet_id = extract_id_from_filename(b.get("filename") or "")

    if llm:
        if llm.get("recommended_timesheet_ids"):
            rec = [x for x in llm["recommended_timesheet_ids"] if x in by_id]
            if rec:
                timesheet_ids = rec
        if not timesheet_ids:
            timesheet_ids = [a["attachment_id"] for a in att_out if a["category"] == "timesheet"]
        if llm.get("recommended_approval_id") in by_id:
            approval_id = llm["recommended_approval_id"]

    # Signed attendance beats audit copy when both present.
    if len(timesheet_ids) > 1:
        signed = [
            aid for aid in timesheet_ids
            if "signed" in (by_id.get(aid, {}).get("filename") or "").lower()
            or "part 1" in (by_id.get(aid, {}).get("filename") or "").lower()
        ]
        if signed:
            timesheet_ids = signed

    return att_out, timesheet_ids, approval_id, sheet_name, sheet_id


def _collect_text(raw: dict) -> str:
    try:
        return raw["choices"][0]["message"]["content"] or ""
    except Exception:
        return ""


async def run_ai_check(db: AsyncSession, email: EmailMessage) -> dict[str, Any]:
    """Analyze email + attachments; return JSON to store on EmailMessage.ai_check."""
    provider = get_email_provider()
    sender_match = await match_sender(
        db, sender_email=email.sender_email, body_text=email.body_text)

    blocks: list[dict[str, Any]] = []
    for a in email.attachments or []:
        aid = a.get("attachment_id")
        fn = a.get("filename") or aid or "attachment"
        ct = a.get("content_type") or "application/octet-stream"
        try:
            data, real_fn, real_ct = await provider.get_attachment_bytes(
                email.provider_message_id, aid)
            text, kind, nested, used_ocr = await _attachment_text(
                real_fn or fn, real_ct or ct, data)
        except Exception as e:
            blocks.append({
                "attachment_id": aid, "filename": fn, "content_type": ct,
                "text": "", "source_kind": "error", "nested": [],
                "category": "other", "reason": f"could not read ({str(e)[:80]})",
                "used_llm": False, "used_ocr": False, "text_chars": 0,
            })
            continue
        from app.services.inbox.timesheet_detect import filename_timesheet_hint
        fn_hint_cat, fn_hint_reason = filename_timesheet_hint(real_fn or fn)
        blocks.append({
            "attachment_id": aid, "filename": real_fn or fn, "content_type": real_ct or ct,
            "text": text, "source_kind": kind, "nested": nested,
            "used_ocr": used_ocr, "text_chars": len((text or "").strip()),
            "filename_hint": (
                f"{fn_hint_cat} — {fn_hint_reason}" if fn_hint_cat else ""
            ),
        })

    llm = await _llm_classify(email, blocks)
    used_llm = llm is not None
    model = settings.ai_check_model if used_llm else None

    att_out, timesheet_ids, approval_id, sheet_name, sheet_id = _reconcile_classification(
        blocks, llm)

    from app.services.inbox.timesheet_detect import plain_email_body

    body_probe = plain_email_body(
        subject=email.subject, body_text=email.body_text, body_html=email.body_html)
    if llm:
        body_cat = str(llm.get("body_category", "other")).strip().lower()
        if body_cat not in _VALID:
            body_cat = "other"
        body_reason = str(llm.get("body_reason") or "").strip()
        # Trust the LLM — it sees the full body text and decides extract_body.
        extract_body = bool(llm.get("extract_body")) and not timesheet_ids
    else:
        body_cat, body_reason = _coded_category(body_probe)
        extract_body = False
        combined = "\n\n".join(b.get("text") or "" for b in blocks)
        if combined.strip() and not sheet_id and not sheet_name:
            from app.services.inbox.timesheet_detect import extract_identity_from_text
            sid, sname = extract_identity_from_text(combined)
            sheet_id, sheet_name = sid or sheet_id, sname or sheet_name

    # Recommended employee: sender email first, then sheet id+name match
    matched: dict | None = sender_match
    if sheet_id or sheet_name:
        m = await employee_matching.match_employee(db, sheet_id, sheet_name)
        if m.employee:
            matched = {
                "employee_pk": m.employee.id,
                "employee_id": m.employee.employee_id,
                "employee_name": m.employee.name,
                "account_manager": m.employee.account_manager,
                "location": m.employee.location,
                "matched_email": sender_match.get("matched_email") if sender_match else None,
                "is_sender": bool(sender_match and sender_match.get("is_sender")),
                "source": "sheet_text",
            }
        elif sender_match and sheet_name:
            emp = (await db.execute(
                select(Employee).where(Employee.id == sender_match["employee_pk"])
            )).scalar_one_or_none()
            if emp:
                m2 = await employee_matching.match_employee(
                    db, None, sheet_name, email_hint=emp)
                if m2.employee:
                    matched = {**sender_match, "source": "sender_email_and_sheet_name"}

    missing: list[str] = []
    found: list[str] = []
    if matched:
        loc = f" ({matched['location']})" if matched.get("location") else ""
        found.append(f"Employee: {matched['employee_name']} · {matched['employee_id']}{loc}")
    else:
        missing.append("No employee matched in matcher")
    if timesheet_ids:
        found.append(f"{len(timesheet_ids)} timesheet attachment(s)")
    elif extract_body:
        found.append("Timesheet in email body")
    else:
        missing.append("No timesheet found")
    if approval_id:
        found.append("Manager approval attachment")
    else:
        missing.append("No approval screenshot (optional)")

    summary = (
        f"{len(timesheet_ids)} timesheet(s)"
        + (", body→image" if extract_body else "")
        + (", approval" if approval_id else "")
    )

    return {
        "summary": summary,
        "model": model,
        "used_llm": used_llm,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "attachments": att_out,
        "body_category": body_cat,
        "body_reason": body_reason,
        "recommended_timesheet_ids": timesheet_ids,
        "recommended_approval_id": approval_id,
        "extract_body": extract_body,
        "matched_employee": matched,
        "missing": missing,
        "found": found,
    }


async def ensure_ai_check(
    db: AsyncSession, email: EmailMessage, *, force: bool = False,
) -> dict[str, Any]:
    if email.ai_check and not force:
        return email.ai_check
    result = await run_ai_check(db, email)
    email.ai_check = result
    email.ai_checked_at = datetime.now(timezone.utc)
    await db.flush()
    return result
