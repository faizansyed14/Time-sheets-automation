"""File-attachment handling for the generic chat.

Deliberately SEPARATE from the app's own extraction pipeline
(services/extract_email, services/bulk_roster) — those render EVERY
document to page images and always read them with a vision model, because a
timesheet's exact layout matters. A chat attachment doesn't need that: the
user just wants the assistant to read what's actually in the file, so this
module reads it directly instead:

  - an actual image file  -> base64, sent to the model as an image block
  - everything else (PDF, DOCX, XLSX, TXT/CSV/anything text-shaped) -> plain
    text extracted straight from the file (PyMuPDF for PDF, python-docx for
    DOCX, openpyxl for XLSX, a plain UTF-8 decode otherwise), sent as a text
    block

No vision model call, no page-image rendering, no LLM extraction step here —
just a direct local read, then the chat model reasons over the text itself
like any other message content. A PDF with no embedded text layer at all (a
scan) simply has nothing to extract — that comes back as a clear error, not
a vision fallback.
"""
from __future__ import annotations

import base64
import io

# Legacy binary formats python-docx/openpyxl cannot open (need a converter
# this module deliberately doesn't carry) — a clear error beats silently
# feeding the model garbage decoded-as-text bytes.
_UNSUPPORTED_LEGACY = {
    ".doc": "Old .doc format isn't supported here — save it as .docx, or use the Upload page.",
    ".xls": "Old .xls format isn't supported here — save it as .xlsx, or use the Upload page.",
}

_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}

# Keep one huge attachment from blowing the context window / cost — a plain
# safety cap, not a design choice the model needs to know about.
MAX_TEXT_CHARS = 100_000

# DOCX/XLSX are zip files under the hood, and neither python-docx nor
# openpyxl guards against a small file that decompresses to something huge
# (a zip bomb). This caps the DECLARED uncompressed size before either
# library ever unpacks it — a lightweight, standard mitigation, not airtight
# against every possible technique, but a real improvement over no check.
MAX_ZIP_UNCOMPRESSED_BYTES = 200 * 1024 * 1024   # 200MB — generous for a real document


def _ext(filename: str) -> str:
    name = (filename or "").lower().strip()
    i = name.rfind(".")
    return name[i:] if i != -1 else ""


def _zip_declares_too_large(data: bytes) -> bool:
    """True only when this IS a valid zip whose own declared uncompressed
    size exceeds the cap. A file that isn't a valid zip at all returns
    False here — that's not this check's job; the real parser below raises
    its own clear "could not read" error for a genuinely corrupt file."""
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return sum(info.file_size for info in zf.infolist()) > MAX_ZIP_UNCOMPRESSED_BYTES
    except Exception:
        return False


def _looks_like_a_real_image(data: bytes) -> bool:
    """Verify the bytes actually decode as an image before we label them
    "image/..." and hand them to the model — an extension/content-type
    alone is attacker-controlled and proves nothing on its own."""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        return True
    except Exception:
        return False


def _extract_pdf_text(data: bytes) -> str:
    """Reconstruct each page's text from the words' own coordinates —
    grouped into rows by y-position, ordered left-to-right within a row —
    rather than trusting page.get_text()'s own block/line reading-order
    heuristic. That heuristic flattens a dense table (many narrow columns,
    e.g. a day-by-day attendance grid) into ONE VALUE PER LINE with no row
    grouping at all: a model reading it then has to count position across
    dozens of disconnected lines to figure out which day a value belongs to
    — exactly the kind of off-by-one misread this replaces. For ordinary
    prose, grouping by y then sorting by x produces the same top-to-bottom,
    left-to-right reading order either way, so this is a strict improvement,
    not a table-only special case."""
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pages_text = []
        for page in doc:
            words = page.get_text("words")   # (x0, y0, x1, y1, text, block, line, word_no)
            if not words:
                continue
            words.sort(key=lambda w: (w[1], w[0]))
            rows: list[list] = []
            row_y = None
            for w in words:
                if row_y is None or w[1] - row_y > 3:
                    rows.append([])
                    row_y = w[1]
                rows[-1].append(w)
            lines = ["\t".join(w[4] for w in sorted(row, key=lambda r: r[0])) for row in rows]
            pages_text.append("\n".join(lines))
        return "\n\n".join(pages_text)
    finally:
        doc.close()


def _extract_docx_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _extract_xlsx_text(data: bytes) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"--- sheet: {ws.title} ---")
        for row in ws.iter_rows(values_only=True):
            if any(v is not None for v in row):
                lines.append(", ".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)


def extract_attachment(filename: str, content_type: str, data: bytes) -> dict:
    """Read one uploaded file for the chat. Returns exactly one of:

      {"kind": "image", "mime": str, "b64": str}
      {"kind": "text", "filename": str, "text": str, "truncated": bool}
      {"kind": "error", "message": str}

    Never raises — any read failure comes back as {"kind": "error", ...} so
    the chat can tell the user plainly instead of the request failing.
    """
    ext = _ext(filename)
    ctype = (content_type or "").lower()

    if ctype.startswith("image/") or ext in _IMAGE_MIME:
        if not _looks_like_a_real_image(data):
            return {"kind": "error", "message": f"{filename} doesn't look like a valid image."}
        mime = _IMAGE_MIME.get(ext) or ctype or "image/png"
        return {"kind": "image", "mime": mime, "b64": base64.b64encode(data).decode("ascii")}

    if ext in _UNSUPPORTED_LEGACY:
        return {"kind": "error", "message": _UNSUPPORTED_LEGACY[ext]}

    if ext in (".docx", ".xlsx", ".xlsm") and _zip_declares_too_large(data):
        return {"kind": "error", "message": f"{filename} is too large to read safely."}

    try:
        if ext == ".pdf":
            text = _extract_pdf_text(data)
        elif ext == ".docx":
            text = _extract_docx_text(data)
        elif ext in (".xlsx", ".xlsm"):
            text = _extract_xlsx_text(data)
        else:
            # .txt, .csv, .md, .json, .log, or anything else not recognised
            # above — a plain UTF-8 decode is the right default, same as
            # dropping a text file into any chat client.
            text = data.decode("utf-8", errors="replace")
    except Exception as e:
        return {"kind": "error", "message": f"Could not read {filename}: {e}"}

    text = (text or "").strip()
    if not text:
        return {"kind": "error", "message": f"No readable text was found in {filename}."}
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]
    return {"kind": "text", "filename": filename, "text": text, "truncated": truncated}
