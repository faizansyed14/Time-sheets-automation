"""
Ported from your project's `file_processor.py`.
Converts uploads to JPEG images for the vision model, and extracts plain text
for the optional text cross-validation step.
"""
from __future__ import annotations

import io
import subprocess
import tempfile
import zipfile
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


def eml_to_images(eml_bytes: bytes) -> list[bytes]:
    from PIL import Image, ImageDraw

    text = extract_document_text("eml", eml_bytes) or "(empty email)"
    img = Image.new("RGB", (1800, 2400), (255, 255, 255))
    d = ImageDraw.Draw(img)
    y = 20
    for ln in text.splitlines()[:120]:
        d.text((20, y), ln[:240], fill=(0, 0, 0))
        y += 20
    return [_to_jpeg_bytes(img)]


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
            from email import policy
            from email.parser import BytesParser
            msg = BytesParser(policy=policy.default).parsebytes(data)
            body = msg.get_body(preferencelist=("plain", "html"))
            return (body.get_content() if body else "").strip()
    except Exception:
        return ""
    return ""
