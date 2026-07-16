"""
Optional OCR "reader" — gives scans/photos a text layer so the same grounding
that makes born-digital sheets accurate also helps images.

Controlled by settings.ocr_provider:
  - "none"      (default) -> no-op, returns "" (behaviour unchanged)
  - "tesseract" -> local Tesseract via pytesseract (FREE, runs locally), if
                   installed. No cloud calls, no per-page cost.

Every path is best-effort: any error returns "" so extraction never breaks.
The returned text is handed to the model as AUTHORITATIVE document text and also
feeds the deterministic grid scan.
"""
from __future__ import annotations

from app.core.config import settings


def ocr_status() -> str:
    """Report whether the configured OCR reader can actually run, so the pipeline
    can surface 'tesseract not installed' instead of silently skipping it.

    Returns one of: "disabled" (OCR_PROVIDER=none), "ready", "not_installed"
    (binding/engine missing), "unknown_provider"."""
    provider = (getattr(settings, "ocr_provider", "none") or "none").strip().lower()
    if provider in ("", "none"):
        return "disabled"
    if provider == "tesseract":
        try:
            import pytesseract  # noqa: F401
            # get_tesseract_version raises if the engine binary is missing.
            pytesseract.get_tesseract_version()
            return "ready"
        except Exception:
            return "not_installed"
    return "unknown_provider"


def ocr_text(images_jpeg: list[bytes], data: bytes, file_type: str) -> str:
    provider = (getattr(settings, "ocr_provider", "none") or "none").strip().lower()
    if provider in ("", "none"):
        return ""
    try:
        if provider == "tesseract":
            return _tesseract(images_jpeg)
    except Exception:
        return ""
    return ""


def _tesseract(images_jpeg: list[bytes]) -> str:
    import io

    import pytesseract
    from PIL import Image

    out: list[str] = []
    for page in (images_jpeg or [])[:10]:
        txt = pytesseract.image_to_string(Image.open(io.BytesIO(page)))
        if txt.strip():
            out.append(txt.strip())
    return "\n\n".join(out).strip()
