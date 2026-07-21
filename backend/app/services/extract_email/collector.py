"""Build SheetUnit lists from .eml bytes."""
from __future__ import annotations

import hashlib
import re

from app.core.config import settings
from app.services.extract_email.constants import MAX_SHEETS
from app.services.extract_email.types import SheetUnit

def unit_from_bytes(name: str, ftype: str, payload: bytes) -> SheetUnit | None:
    """One document → digital text (OCR fallback for scans) + an image, UNLESS
    it's a PDF/DOCX/XLSX going to OpenAI — those upload natively (their raw
    bytes travel as-is in `payload`; see vision_client._openai_by_files), so
    no client-side render is spent on them at all."""
    from app.services.extraction import ocr, vision_client
    from app.services.extraction.file_processor import extract_document_text, to_images

    skip_image = ftype in vision_client.NATIVE_FILE_TYPES and vision_client.vision_provider() == "openai"
    images: list[bytes] = []
    if not skip_image:
        try:
            # Always one image per sheet (multi-page PDFs are stitched upstream).
            images = (to_images(ftype, payload) or [])[:1]
        except Exception:
            images = []
    text = ""
    try:
        text = extract_document_text(ftype, payload) or ""
    except Exception:
        pass
    if not text.strip() and images and ocr.ocr_status() == "ready":
        try:
            text = ocr.ocr_text(images, payload, ftype) or ""
        except Exception:
            pass
    if skip_image or images or text.strip():
        from app.services.extract_email.formats import detect_format
        fmt = detect_format(text or "", name or "")
        return SheetUnit(name or "attachment", ftype, payload, images, text.strip(),
                         format_id=fmt.id)
    return None


_HTML_TABLE_RE = re.compile(r"<table\b", re.I)


def body_unit(
    eml_bytes: bytes,
    subject: str | None,
    body_text: str | None,
    body_html: str | None = None,
) -> SheetUnit | None:
    """The email body as its own sheet.

    PII-scrubbed body text is sent DIRECTLY as text — every provider reads it
    natively, and the system prompt's own body-grid detection is text-first
    already ("read the grid rows... pasted lower in the thread"). No image
    render, no vision call spent on it.

    The ONE fallback to rendering an image: the HTML body contains a real
    <table> — a genuinely pasted HTML grid, where naive tag-stripping can run
    cells together (no whitespace between adjacent <td>s) and a rendered image
    preserves the visual layout the model needs. Plain text / <br>-separated
    bodies (the overwhelming majority) never hit this branch.
    """
    from app.core.pii import scrub_email_for_llm
    from app.services.extraction.file_processor import _extract_eml_body_text

    body = (body_text or "").strip()
    if not body and not eml_bytes:
        return None
    _subj, scrubbed_body = scrub_email_for_llm(subject, body_text)
    if eml_bytes:
        try:
            eml_plain = _extract_eml_body_text(eml_bytes)
            if eml_plain.strip():
                _subj, scrubbed_from_eml = scrub_email_for_llm(subject, eml_plain)
                if len(scrubbed_from_eml.strip()) >= len(scrubbed_body.strip()):
                    scrubbed_body = scrubbed_from_eml
        except Exception:
            pass
    if not scrubbed_body.strip():
        return None

    if body_html and _HTML_TABLE_RE.search(body_html):
        from app.services.extraction.file_processor import email_body_to_images, eml_body_to_images
        try:
            if eml_bytes:
                imgs = (eml_body_to_images(eml_bytes) or [])[:1]
                if not imgs and body:
                    imgs = (email_body_to_images(subject, body_text) or [])[:1]
            elif body:
                imgs = (email_body_to_images(subject, body_text) or [])[:1]
            else:
                imgs = []
        except Exception:
            imgs = []
        if imgs:
            return SheetUnit("(email body)", "image", imgs[0],
                             imgs, scrubbed_body[:12000])

    return SheetUnit("(email body)", "text", scrubbed_body.encode("utf-8"),
                     [], scrubbed_body[:12000])


def collect_units(email, eml_bytes: bytes) -> list[SheetUnit]:
    """EVERY document inside the .eml (attachments, forwarded emails and their
    attachments) plus the email body — the vision model decides what each
    sheet is. Logos / banners / signature icons never become units (they are
    stripped in eml_all_attachments / is_decorative_image). Tiny images under
    MIN_IMAGE_ATTACHMENT_KB are also skipped as a belt-and-suspenders check."""
    from app.services.extraction.file_processor import eml_all_attachments, is_decorative_image

    min_img = settings.min_image_attachment_kb * 1024
    units: list[SheetUnit] = []
    raw = eml_all_attachments(eml_bytes)[:MAX_SHEETS]
    has_doc = any(ft in ("pdf", "docx", "xlsx", "eml") for _, _, ft in raw)
    for name, payload, ftype in raw:
        if ftype == "image":
            if 0 < len(payload or b"") < min_img:
                continue
            if is_decorative_image(name, payload or b"", has_doc=has_doc):
                continue
        u = unit_from_bytes(name, ftype, payload)
        if u:
            units.append(u)
    bu = body_unit(eml_bytes, email.subject, email.body_text, getattr(email, "body_html", None))
    if bu:
        units.append(bu)
    return units


def content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload or b"").hexdigest()


def merge_thread_units(primary: list[SheetUnit], secondary: list[SheetUnit]) -> list[SheetUnit]:
    """Combine sheets from two thread messages for thread-aware extraction,
    deduplicating attachments that were re-sent/forwarded byte-identical (so
    the same PDF is never analysed twice). Body sheets from BOTH messages are
    always kept — their content differs, and approval wording (e.g. a reply
    that says "Approved." with no re-attached file) can land in either one."""
    seen = {content_hash(u.payload) for u in primary if u.payload}
    merged = list(primary)
    for u in secondary:
        if u.payload and content_hash(u.payload) in seen:
            continue  # identical bytes already covered by the primary message
        merged.append(u)
    return merged
