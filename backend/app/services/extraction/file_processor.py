"""
Ported from your project's `file_processor.py`.
Converts uploads to JPEG images for the vision model, and extracts plain text
for the optional text cross-validation step.
"""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import zipfile
from html import unescape
from pathlib import Path

PDF_DPI = 300
PDF_MAX_PAGES = 10
JPEG_QUALITY = 90
IMAGE_MAX_SIDE = 2200


def _to_jpeg_bytes(img, quality: int = JPEG_QUALITY) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def detect_file_type(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff"):
        return "image"
    if data[:2] == b"PK" and zipfile.is_zipfile(io.BytesIO(data)):
        if name.endswith(".docx"):
            return "docx"
        if name.endswith(".xlsx"):
            return "xlsx"
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    if name.endswith(".xlsx"):
        return "xlsx"
    if name.endswith(".eml"):
        return "eml"
    if name.endswith((".png", ".jpg", ".jpeg")):
        return "image"
    return "unknown"


def pdf_to_images(pdf_bytes: bytes, dpi: int = PDF_DPI, max_pages: int = PDF_MAX_PAGES) -> list[bytes]:
    import fitz  # PyMuPDF
    from PIL import Image

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images: list[bytes] = []
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    for i in range(min(doc.page_count, max_pages)):
        page = doc.load_page(i)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(_to_jpeg_bytes(img))
    doc.close()
    return images


def image_to_images(image_bytes: bytes, max_side: int = IMAGE_MAX_SIDE) -> list[bytes]:
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return [_to_jpeg_bytes(img)]


def _office_to_pdf_bytes(file_bytes: bytes, ext: str) -> bytes | None:
    """Convert docx/xlsx -> PDF via LibreOffice headless, if soffice is installed."""
    if not file_bytes or ext not in {"docx", "xlsx"}:
        return None
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        in_path = td_path / f"input.{ext}"
        out_dir = td_path / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        in_path.write_bytes(file_bytes)
        cmd = ["soffice", "--headless", "--nologo", "--nofirststartwizard", "--norestore",
               "--convert-to", "pdf", "--outdir", str(out_dir), str(in_path)]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=90)
        except Exception:
            return None
        pdfs = list(out_dir.glob("*.pdf"))
        return pdfs[0].read_bytes() if pdfs else None


def docx_to_images(docx_bytes: bytes) -> list[bytes]:
    from PIL import Image, ImageDraw

    pdf = _office_to_pdf_bytes(docx_bytes, "docx")
    if pdf:
        return pdf_to_images(pdf)
    # fallback: render extracted text
    from docx import Document
    doc = Document(io.BytesIO(docx_bytes))
    lines = [p.text for p in doc.paragraphs if p.text.strip()]
    for t in doc.tables:
        for row in t.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    text = "\n".join(lines[:400]) or "(empty DOCX)"
    img = Image.new("RGB", (1800, 2400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    y = 20
    for ln in text.splitlines():
        d.text((20, y), ln[:240], fill=(0, 0, 0))
        y += 20
    return [_to_jpeg_bytes(img)]


def xlsx_to_images(xlsx_bytes: bytes) -> list[bytes]:
    from PIL import Image, ImageDraw

    pdf = _office_to_pdf_bytes(xlsx_bytes, "xlsx")
    if pdf:
        return pdf_to_images(pdf)
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    ws = wb.active
    lines = []
    for r in range(1, min(ws.max_row or 1, 400) + 1):
        vals = [("" if ws.cell(r, c + 1).value is None else str(ws.cell(r, c + 1).value))
                for c in range(min(ws.max_column or 1, 40))]
        if any(vals):
            lines.append(" | ".join(vals))
    text = "\n".join(lines) or "(empty XLSX)"
    img = Image.new("RGB", (2400, 3000), (255, 255, 255))
    d = ImageDraw.Draw(img)
    y = 20
    for ln in text.splitlines():
        d.text((20, y), ln[:340], fill=(0, 0, 0))
        y += 18
    return [_to_jpeg_bytes(img)]


_TIMESHEET_MARKERS = (
    "emp no", "employee id", "employee no", "emp id", "subject: timesheet", "timesheet -",
)


def _parse_eml(eml_bytes: bytes):
    from email import policy
    from email.parser import BytesParser

    return BytesParser(policy=policy.default).parsebytes(eml_bytes)


def _html_to_text(html: str) -> str:
    """Flatten HTML tables into pipe-separated rows for the vision / text steps."""
    h = html or ""
    h = re.sub(r"(?i)</tr\s*>", "\n", h)
    h = re.sub(r"(?i)</t[dh]\s*>", " | ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    text = unescape(h)
    lines: list[str] = []
    for ln in text.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def _score_timesheet_text(text: str) -> int:
    low = (text or "").lower()
    score = 0
    for marker in _TIMESHEET_MARKERS:
        if marker in low:
            score += 50
    for kw in ("sick leave", "annual leave", "public holiday", "weekend", "work from home", "wfh"):
        score += low.count(kw) * 5
    score += min(len(text) // 200, 40)
    return score


def _extract_eml_body_text(eml_bytes: bytes) -> str:
    """Best-effort body text from an .eml (plain + HTML parts, reply chains included)."""
    try:
        msg = _parse_eml(eml_bytes)
    except Exception:
        return ""

    chunks: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        ct = part.get_content_type()
        try:
            if ct == "text/plain":
                chunks.append(part.get_content())
            elif ct == "text/html":
                chunks.append(_html_to_text(part.get_content()))
        except Exception:
            continue

    if not chunks:
        return ""

    return max(chunks, key=_score_timesheet_text).strip()


def _focus_timesheet_text(text: str) -> str:
    """Drop reply headers / signatures; keep the forwarded timesheet block."""
    lines = text.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        low = ln.lower().strip()
        if any(m in low for m in _TIMESHEET_MARKERS):
            start = i
            break
    focused = "\n".join(lines[start:]).strip()
    return focused or text.strip()


_INLINE_LOGO_MAX_BYTES = 20_000  # skip small inline images (email logos)


def _part_file_type(part, payload: bytes) -> str | None:
    """Resolve file type from filename, content-type, or magic bytes."""
    filename = part.get_filename() or ""
    ftype = detect_file_type(filename, payload)
    if ftype != "unknown":
        return ftype
    ct = (part.get_content_type() or "").lower()
    if ct == "application/pdf" or payload.startswith(b"%PDF"):
        return "pdf"
    if ct in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        return "docx"
    if ct in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ):
        return "xlsx"
    if ct.startswith("image/"):
        return "image"
    return None


def _score_eml_attachment(filename: str, payload: bytes, ftype: str) -> int:
    fn = (filename or "").lower()
    score = 0
    if any(k in fn for k in ("timesheet", "time sheet", "time-sheet")):
        score += 120
    if ftype == "pdf":
        score += 60
    elif ftype in ("docx", "xlsx"):
        score += 50
    elif ftype == "image":
        score += 20
    score += min(len(payload) // 8000, 40)
    return score


def _eml_collect_attachments(eml_bytes: bytes) -> list[tuple[str, bytes, str]]:
    """Return (filename, payload, file_type) for real timesheet attachments inside .eml."""
    try:
        msg = _parse_eml(eml_bytes)
    except Exception:
        return []

    found: list[tuple[str, bytes, str]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue

        filename = part.get_filename() or ""
        disposition = (part.get_content_disposition() or "").lower()
        ftype = _part_file_type(part, payload)
        if not ftype:
            continue

        if disposition == "attachment":
            found.append((filename, payload, ftype))
            continue

        # Inline: skip logos; allow large inline PDFs / sheet images
        if disposition == "inline":
            if ftype in ("pdf", "docx", "xlsx"):
                found.append((filename, payload, ftype))
            elif ftype == "image" and len(payload) > _INLINE_LOGO_MAX_BYTES:
                found.append((filename, payload, ftype))
            continue

        # No disposition — keep only recognisable document payloads
        if ftype in ("pdf", "docx", "xlsx"):
            found.append((filename, payload, ftype))

    return found


def _text_to_page_images(
    text: str,
    *,
    width: int = 2400,
    height: int = 3200,
    line_h: int = 18,
    char_w: int = 340,
    lines_per_page: int = 160,
) -> list[bytes]:
    from PIL import Image, ImageDraw

    lines = [ln for ln in (text or "").splitlines() if ln.strip()] or ["(empty email)"]
    images: list[bytes] = []
    for offset in range(0, len(lines), lines_per_page):
        page_lines = lines[offset : offset + lines_per_page]
        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        y = 20
        for ln in page_lines:
            d.text((20, y), ln[:char_w], fill=(0, 0, 0))
            y += line_h
            if y > height - 30:
                break
        images.append(_to_jpeg_bytes(img))
    return images


def eml_best_attachment(eml_bytes: bytes) -> tuple[str, bytes, str] | None:
    """Pick the single most likely timesheet file when an .eml has several attachments."""
    attachments = _eml_collect_attachments(eml_bytes)
    if not attachments:
        return None
    return max(attachments, key=lambda t: _score_eml_attachment(t[0], t[1], t[2]))


def eml_attachment_save_name(filename: str, ftype: str) -> str:
    name = (filename or "").strip()
    if name:
        return name
    ext = {"pdf": ".pdf", "docx": ".docx", "xlsx": ".xlsx", "image": ".png"}.get(ftype, "")
    return f"extracted_timesheet{ext}"


def _eml_attachment_images(eml_bytes: bytes) -> list[bytes]:
    """PDF / Office / large inline images attached to the email (not logos)."""
    best = eml_best_attachment(eml_bytes)
    if not best:
        return []
    _name, payload, ftype = best
    try:
        return to_images(ftype, payload)
    except Exception:
        return []


def eml_to_images(eml_bytes: bytes) -> list[bytes]:
    # 1) attached PDF / Office sheet (most reliable across varying email layouts)
    attached = _eml_attachment_images(eml_bytes)
    if attached:
        return attached
    # 2) timesheet embedded in forwarded email body (plain / HTML tables)
    text = _focus_timesheet_text(_extract_eml_body_text(eml_bytes)) or "(empty email)"
    return _text_to_page_images(text)


def to_images(file_type: str, data: bytes) -> list[bytes]:
    if file_type == "pdf":
        return pdf_to_images(data)
    if file_type == "docx":
        return docx_to_images(data)
    if file_type == "xlsx":
        return xlsx_to_images(data)
    if file_type == "eml":
        return eml_to_images(data)
    if file_type == "image":
        return image_to_images(data)
    raise ValueError(f"Unsupported file type: {file_type}")


def extract_document_text(file_type: str, data: bytes) -> str:
    """Plain text for the optional text cross-validation step."""
    try:
        if file_type == "pdf":
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            parts = []
            for i in range(min(doc.page_count, PDF_MAX_PAGES)):
                parts.append(doc.load_page(i).get_text())
            doc.close()
            return "\n".join(parts).strip()
        if file_type == "docx":
            from docx import Document
            doc = Document(io.BytesIO(data))
            lines = [p.text for p in doc.paragraphs if p.text.strip()]
            for t in doc.tables:
                for row in t.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        lines.append(" | ".join(cells))
            return "\n".join(lines).strip()
        if file_type == "xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), data_only=True)
            ws = wb.active
            lines = []
            for r in range(1, min(ws.max_row or 1, 500) + 1):
                vals = [("" if ws.cell(r, c + 1).value is None else str(ws.cell(r, c + 1).value))
                        for c in range(min(ws.max_column or 1, 50))]
                if any(vals):
                    lines.append(" | ".join(vals))
            return "\n".join(lines).strip()
        if file_type == "eml":
            best = eml_best_attachment(data)
            if best:
                _name, payload, ftype = best
                doc_text = extract_document_text(ftype, payload)
                if doc_text.strip():
                    return doc_text.strip()
            return _focus_timesheet_text(_extract_eml_body_text(data))
    except Exception:
        return ""
    return ""
