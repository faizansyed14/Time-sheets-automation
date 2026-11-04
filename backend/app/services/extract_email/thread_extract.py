"""Whole-thread extraction — a two-pass vision read over an entire conversation.

Ported from the prompt lab (docs/timesheet_strong_prompt.ipynb): every message
body, every attachment, and any email/message attached inside another one
becomes its own `Item` with a stable [A#] key. Then:

  PASS 1 — CLASSIFY (triage_prompt.py). Judges what each item IS: timesheet,
  leave evidence, approval evidence, other, or noise — by structure, not by
  matching a catalogue of known client templates. Also writes the one
  plain-English thread summary.

  PASS 2 — EXTRACT (thread_prompt.py). Reads only the items pass 1 confirmed,
  transcribing not just leave but EVERY day of the period (working, weekend,
  leave, or genuinely uncertain) — a full day-by-day account, not just a scan
  for leave.

Pass 1 batches by ITEM COUNT (PASS1_BATCH_SIZE); pass 2 batches by IMAGE COUNT
(MAX_IMAGES_PER_CALL) — a long thread with many confirmed sheets is split
across several calls per pass rather than overflowing one request.

There is no fallback: a vision model is mandatory (see the RuntimeError below
if none is configured) and no per-sheet/template/mock pipeline exists anymore.
"""
from __future__ import annotations

import calendar
import email as _email
import email.policy
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.pii import scrub_text
from app.services.extract_email.constants import BUCKETS
from app.services.extract_email.progress import count_llm, emit

log = logging.getLogger("extract_email.msg")

# A forward of a forward of a forward is real; beyond this it is a mail loop,
# not a timesheet. Applies to .eml/.msg attached as a FILE (not MIME-nested —
# those are unwrapped for free by the normal MIME walk).
MAX_EML_DEPTH = 12          # default; the live value is settings.max_eml_depth
# A picture under this many bytes cannot be a readable document (a 1x1 tracking
# pixel, a bullet). The ONLY unconditional size drop; everything above it is
# judged by what it is (inline vs attachment, OCR text), not by a size guess.
_ABSOLUTE_MIN_IMAGE_BYTES = 1024
# Logos / signature banners / social icons are small pictures that are either EMBEDDED in
# the body (cid) or carry a generic mail-client name (image001.png, Outlook-xxxx.png).
# Such a picture under settings.min_image_bytes (30 KB) is dropped - recorded, with its
# thumbnail - before any model call. A picture the sender deliberately attached under a
# real file name ("5thSept-11sept.png") is always read, however small.
_GENERIC_IMAGE_NAME = re.compile(
    r"^(image\d{0,3}\.(png|jpe?g|gif|bmp)|outlook-.+\.(png|jpe?g|gif|bmp)"
    r"|c2_signature_.+\.(png|jpe?g|gif)|noname.*|unnamed.*|)$", re.I)


def _is_generic_image_name(name: str | None) -> bool:
    return bool(_GENERIC_IMAGE_NAME.match((name or "").strip()))
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")

SUMMARY_STATUSES = (
    "sheet_submitted", "awaiting_approval", "approved",
    "correction_requested", "chasing", "other",
)


@dataclass
class Item:
    """One analysable thing in the thread: an attachment, or a message body."""

    key: str                                  # "A1", "A2", ... — stable, model must echo it back
    name: str                                 # filename, or "email body (message N)"
    mime: str
    msg_index: int                            # which message in the thread it came from
    text: str = ""                            # extracted/native text (body, xlsx, docx, embedded PDF text)
    images: list[bytes] = field(default_factory=list)   # rendered page JPEGs
    inline: bool = False                      # embedded in body (cid) vs a real attachment
    size: int = 0
    send_mode: str = "n/a"
    digest: str = ""                          # sha256 of the original bytes — Extracted/New badge only


@dataclass
class Message:
    index: int
    depth: int
    subject: str
    frm: str
    to: str
    date: str
    body: str = ""


@dataclass
class Thread:
    subject: str
    messages: list[Message] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)   # items filtered out (size/OCR noise)
    # Outer .eml/.msg filenames that were unwrapped (Inbox lists these; the
    # analysable items are the PDFs/images inside). Recorded for Extracted/New
    # badges so nested containers don't look "New" after a successful run.
    opened_containers: list[dict] = field(default_factory=list)
    # Things that could not be read AT ALL (corrupt file, nesting too deep, a
    # page that would not render). Permanent, so they are WARNINGS — shown to
    # the reviewer on every staged item — not retriable errors.
    unreadable: list[str] = field(default_factory=list)

    @property
    def n_images(self) -> int:
        return sum(len(i.images) for i in self.items)


def resolve_source(source: str, items: list[Item]) -> Item | None:
    """Map a pass-1/pass-2 `source` label ("[A#] name" or a bare name) back to
    its Item. The [A#] key is unambiguous by construction; the name-matching
    fallbacks only cover a model that dropped the key from its answer."""
    if not source:
        return None

    s = str(source).strip()
    m = re.search(r"A(\d+)", s)
    if m:
        for it in items:
            if it.key.lower() == f"a{m.group(1)}":
                return it
    low = s.lower()
    for it in items:
        if it.name.lower() == low:
            return it
    for it in items:
        if it.name.lower() in low or low in it.name.lower():
            return it
    return None


def _content_digest(payload: bytes) -> str:
    return hashlib.sha256(payload or b"").hexdigest()


def msg_to_eml_bytes(payload: bytes) -> bytes | None:
    """Outlook .msg -> raw .eml bytes, via extract-msg. Returns None if the
    file can't be parsed (corrupt / not really a .msg) — every caller treats
    that as "skip this one," so the real reason must reach the server log
    (log.exception below) instead of vanishing silently; a caller-facing
    error message can only ever say "could not parse," not why.

    asEmailMessage() — NOT export()/exportBytes() — is the correct call
    here: export()/exportBytes() re-save the SAME .msg (OLE) format, which
    is useless for feeding the RFC822 (.eml) reader below; export() also
    requires a destination path argument and returns None, so calling it
    with none (as this function used to) raised TypeError on every single
    .msg, unconditionally — silently swallowed by the broad except below,
    so .msg conversion had never actually worked. asEmailMessage() returns a
    real stdlib EmailMessage, which .as_bytes() serializes to genuine
    RFC822 bytes the rest of this module already knows how to read."""
    import tempfile

    try:
        import extract_msg
    except ImportError:
        log.warning("extract-msg is not installed — cannot convert .msg to .eml")
        return None
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as f:
            f.write(payload)
            path = f.name
        msg = extract_msg.Message(path)
        try:
            return msg.asEmailMessage().as_bytes()
        finally:
            msg.close()
    except Exception:
        log.exception("Could not convert .msg to .eml (%d bytes)", len(payload or b""))
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _max_depth() -> int:
    return max(1, int(getattr(settings, "max_eml_depth", MAX_EML_DEPTH) or MAX_EML_DEPTH))


def _page_limit() -> int:
    """Pages rendered per document. settings.pdf_max_pages <= 0 means NO cap —
    the default, because a page silently never read is a leave day silently
    never booked. A positive value is an explicit operator choice."""
    n = int(getattr(settings, "pdf_max_pages", 0) or 0)
    return n if n > 0 else 10_000


def _pdf_pages(payload: bytes) -> tuple[list[bytes], str]:
    """Per-page JPEGs (one image per page, never stitched) + the PDF's own
    concatenated text layer (every page unless pdf_max_pages is set)."""
    import fitz

    from app.services.extraction.file_processor import pdf_to_page_images

    texts: list[str] = []
    try:
        doc = fitz.open(stream=payload, filetype="pdf")
        try:
            for i in range(min(doc.page_count, _page_limit())):
                texts.append(doc.load_page(i).get_text("text") or "")
        finally:
            doc.close()
    except Exception:
        texts = []
    try:
        images = pdf_to_page_images(payload, dpi=settings.pdf_render_dpi, max_pages=_page_limit())
    except Exception:
        images = []
    text = scrub_text("\n\n".join(t.strip() for t in texts if t.strip()))
    return images, text


def _fill_pdf_item(it: Item, payload: bytes) -> None:
    images, text = _pdf_pages(payload)
    it.text = text
    if settings.attachment_mode == "native":
        if len(text.strip()) >= 20:
            it.send_mode = "native"
            return
        it.images = images
        it.send_mode = "image (fallback - no text layer)"
        return
    it.images = images
    it.send_mode = "image"


def _office_pages(payload: bytes, ext: str) -> list[bytes]:
    """xlsx/docx -> page images via headless LibreOffice (PDF as the
    intermediate step, then the same per-page renderer as a native PDF).
    Only attempted when ATTACHMENT_MODE is 'image' and USE_LIBREOFFICE is on."""
    if settings.attachment_mode != "image" or not settings.use_libreoffice:
        return []
    from app.services.extraction.file_processor import _office_to_pdf_bytes, pdf_to_page_images

    body = payload
    if ext.lstrip(".") == "xlsx":
        from app.services.extraction.file_processor import _autofit_xlsx_columns
        body = _autofit_xlsx_columns(body)
    try:
        pdf = _office_to_pdf_bytes(body, ext.lstrip("."))
    except Exception:
        pdf = None
    if not pdf:
        return []
    try:
        return pdf_to_page_images(pdf, dpi=settings.pdf_render_dpi, max_pages=_page_limit())
    except Exception:
        return []


def _fill_office_item(it: Item, payload: bytes, ftype: str, ext: str) -> None:
    from app.services.extraction.file_processor import extract_document_text

    try:
        it.text = scrub_text(extract_document_text(ftype, payload) or "")
    except Exception:
        it.text = ""
    images = _office_pages(payload, ext)
    it.images = images
    it.send_mode = "image + native text" if images else "native"


def _image_jpeg(payload: bytes) -> bytes | None:
    from app.services.extraction.file_processor import image_to_images

    try:
        out = image_to_images(payload, max_side=settings.img_max_dim)
        return out[0] if out else None
    except Exception:
        return None


def _thumb_uri(jpeg_bytes: bytes | None, *, max_side: int = 96) -> str | None:
    """Tiny JPEG data-URI for the live Extract UI. Returns None on failure."""
    if not jpeg_bytes:
        return None
    import base64
    import io

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        w, h = img.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=45, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _item_thumb(it: Item) -> str | None:
    return _thumb_uri(it.images[0]) if it.images else None


def _is_email_body(it: Item) -> bool:
    return (it.name or "").lower().startswith("email body")


# Words that make a plain-text message worth a second look when the quick first
# pass filed it as "other": leave / absence / attendance vocabulary in English
# and the common Arabic terms. Deliberately broad - a false hit costs one extra
# model call and at worst an item a reviewer dismisses; a miss loses leave data.
_LEAVE_VOCAB = re.compile(
    r"leave|sick|vacation|holiday|absent|absence|\boff\b|attendance|timesheet|time sheet|"
    r"annual|medical|maternity|paternity|compassionate|unpaid|casual|"
    r"إجازة|اجازة|مرض|غياب|دوام",
    re.IGNORECASE)


def _wants_second_look(it: Item) -> bool:
    """Whether an item the first pass filed as other/noise is re-checked alone.
    Every attachment with pictures or native text (screenshots, scans, PDFs,
    spreadsheets, documents) qualifies; a message body qualifies when it uses
    leave / attendance vocabulary, so ordinary conversation is not re-read."""
    if _is_email_body(it):
        return bool(it.text and _LEAVE_VOCAB.search(it.text))
    return bool(it.images or it.text)


def _record_dropped(th: Thread, entry: dict, image: bytes | None) -> None:
    """Append to th.dropped (unchanged, powers the live Extract UI's ≤96px
    thumbnail) and — only when a debug capture is active (see
    debug_capture.py) — also save the FULL-resolution image so a debug run
    can show what was actually dropped, not just a thumbnail."""
    th.dropped.append(entry)
    from app.services.extract_email import debug_capture
    cap = debug_capture.get_capture()
    if cap is None:
        return
    # Held in memory, NOT written here: the caller saves it only if this run ends
    # up in the debug log (see DebugCapture.save_images / ThreadAgent).
    held = (f"dropped-{len(cap.dropped_items)}-{entry.get('name') or 'item'}", image) if image else None
    debug_capture.record_dropped(**entry, image_path=None, _image=held)


def _dedupe_sheet_items(th: Thread) -> None:
    """Collapse byte-identical sheet attachments ONLY.

    Exact same bytes -> keep one (zero information loss either way, so which
    copy survives is a harmless tie-break, not a guess).

    This used to ALSO collapse "near-identical" text — e.g. an unsigned
    timesheet vs. the same sheet regenerated after manager sign-off — keeping
    whichever scored higher on a keyword count (_approval_signal_score:
    "approved", "signature", "manager", ...) and silently discarding the
    other with no reviewer-visible flag. That heuristic is gone: a BLANK,
    unsigned template routinely has those exact words pre-printed as empty
    field labels ("Approved by: ______"), so it can score as high as — or
    higher than — a genuinely signed copy that OCR'd poorly (e.g. a lower-
    quality scan/photo). Confirmed in production: the signed copy lost to
    the unsigned one, and nothing ever told the reviewer a second file
    existed. Two sheets that merely LOOK similar are no longer a case this
    function decides on its own — both now survive into pass 1/2 and
    grouping, where grouping.py's own overlap flag ("two sheets claim the
    same days") surfaces it for a human instead of an algorithm silently
    picking a winner. Email bodies are never merged.
    """
    keepers: list[Item] = []
    for it in th.items:
        if _is_email_body(it):
            keepers.append(it)
            continue
        matched_idx: int | None = None
        for i, kept in enumerate(keepers):
            if _is_email_body(kept):
                continue
            if it.digest and kept.digest and it.digest == kept.digest:
                matched_idx = i
                break
        if matched_idx is None:
            keepers.append(it)
            continue
        kept = keepers[matched_idx]
        # Byte-identical only, from here on — genuinely the same file, so
        # preferring the later message (e.g. a plain resend) is a harmless
        # tie-break, never a signal-guessing decision.
        if it.msg_index > kept.msg_index:
            winner, loser = it, kept
            keepers[matched_idx] = it
        else:
            winner, loser = kept, it
        _record_dropped(th, {
            "name": loser.name,
            "mime": loser.mime,
            "size": loser.size,
            "msg_index": loser.msg_index,
            "key": loser.key,
            "filter": "dup",
            "reason": f"duplicate of [{winner.key}] {winner.name} (identical bytes)",
            "kept_key": winner.key,
            "kept_name": winner.name,
            "thumb": _item_thumb(loser),
        }, loser.images[0] if loser.images else None)
    th.items = keepers


def collect_thread(messages: list[tuple[str, bytes]]) -> Thread:
    """Build the Item/Message/Thread model for a whole conversation.

    `messages` is [(label, eml_bytes)] oldest first — every message of the
    conversation already fetched as its own raw .eml (see
    thread_collect.collect_thread_emls). Each is walked for its MIME tree,
    descending into forwarded messages (MIME-nested message/rfc822 parts, or
    an .eml/.msg attached as a plain FILE — Outlook does both) up to
    MAX_EML_DEPTH, so a timesheet buried inside a forward-of-a-forward is
    never silently dropped.
    """
    from app.services.extraction.file_processor import _html_to_text, detect_file_type

    th = Thread(subject=messages[0][0] if messages else "(no subject)")
    counter = {"n": 0}

    def new_key() -> str:
        counter["n"] += 1
        return f"A{counter['n']}"

    def add_attachment(name: str, payload: bytes, ctype: str, msg_index: int,
                        inline: bool, depth: int) -> None:
        if not payload:
            return
        # Malformed/truncated MIME headers (RFC 2047 encoded-words, RFC 2231
        # filename* continuations) occasionally decode a filename OR a
        # content-type string containing an embedded NUL byte (seen live:
        # "image/jpeg\u0000" from get_content_type() on a deeply-nested
        # forwarded part). That's syntactically valid JSON (\u0000) so it
        # writes fine into extraction_meta, but Postgres can't convert it
        # back to text on ANY later read of that json/jsonb document — strip
        # BOTH fields here, at the source, before either reaches
        # "skipped"/"noise"/etc. (name-only was the Oct-6 fix; ctype slipped
        # through and re-poisoned the same row via its "mime" field).
        name = name.replace("\x00", "") if name else name
        ctype = ctype.replace("\x00", "") if ctype else ctype
        ext = os.path.splitext(name)[1].lower()
        is_image = ext in IMAGE_EXTS or (ctype or "").startswith("image/")

        # ZIP archives are never extracted — checked BEFORE detect_file_type,
        # not after: an xlsx/docx file IS a zip container internally, and
        # detect_file_type's own zip-sniffing only tells them apart by
        # checking whether an internal path starts with "xl/"/"word/". A
        # genuine .zip whose contents coincidentally include a top-level
        # "xl" or "word" folder would otherwise be misdetected as a real
        # Office document and handed to openpyxl/LibreOffice. A real xlsx/
        # docx attachment never carries ".zip"/a zip content-type, so this
        # can never false-positive on an actual timesheet.
        if ext == ".zip" or (ctype or "").lower() in ("application/zip", "application/x-zip-compressed"):
            _record_dropped(th, {
                "name": name, "mime": ctype, "size": len(payload), "msg_index": msg_index,
                "filter": "zip",
                "reason": "ZIP archives are never extracted.",
            }, None)
            return

        # Size filter FIRST — logos, signature icons and social buttons never
        # reach any further processing. Judged by WHAT the picture is, not a
        # bare byte count: a real weekly-timesheet screenshot is often well
        # under any size threshold. Only an EMBEDDED (cid) image is a
        # logo/stamp suspect, and even then it is kept below if OCR finds real
        # text in it; a real attachment is always read.
        small_logo = bool(
            settings.min_image_bytes and len(payload) < settings.min_image_bytes
            and (inline or _is_generic_image_name(name)))
        if is_image and (len(payload) < _ABSOLUTE_MIN_IMAGE_BYTES or small_logo):
            px = _image_jpeg(payload)
            _record_dropped(th, {
                "name": name, "mime": ctype, "size": len(payload), "msg_index": msg_index,
                "filter": "size",
                "reason": (f"image under {_ABSOLUTE_MIN_IMAGE_BYTES} bytes (tracking pixel / bullet)"
                           if len(payload) < _ABSOLUTE_MIN_IMAGE_BYTES else
                           f"{'embedded' if inline else 'generically named'} image under "
                           f"{settings.min_image_bytes // 1024}KB (logo / signature / icon)"),
                "thumb": _thumb_uri(px),
            }, px)
            return

        # An email attached as a FILE (not MIME-nested) — unwrap recursively,
        # same as a forward the mail structure already nests for free.
        if ext in (".eml", ".msg"):
            if depth + 1 > _max_depth():
                th.unreadable.append(
                    f"{name or 'attached email'}: nested {depth + 1} levels deep — not opened.")
                return
            sub_bytes = msg_to_eml_bytes(payload) if ext == ".msg" else payload
            if not sub_bytes:
                th.unreadable.append(
                    f"{name or 'attached email'}: could not be opened (corrupt or unsupported {ext}).")
                return
            try:
                sub = _email.message_from_bytes(sub_bytes, policy=_email.policy.default)
            except Exception:
                th.unreadable.append(f"{name or 'attached email'}: could not be parsed.")
                return
            # Inbox shows THIS outer filename; remember it so the Extracted
            # badge matches what the user sees (inner PDF names alone don't).
            th.opened_containers.append({
                "name": name or "unnamed",
                "digest": _content_digest(payload),
            })
            walk(sub, depth + 1)
            return

        ftype = detect_file_type(name, payload)
        it = Item(key=new_key(), name=name or "unnamed", mime=ctype or "", msg_index=msg_index,
                  inline=inline, size=len(payload), digest=_content_digest(payload))

        if ext == ".pdf" or ftype == "pdf":
            _fill_pdf_item(it, payload)
        elif is_image or ftype == "image":
            jpeg = _image_jpeg(payload)
            if jpeg is None:
                _record_dropped(th, {
                    "name": name, "mime": ctype, "size": len(payload), "msg_index": msg_index,
                    "filter": "unreadable",
                    "reason": "image could not be decoded",
                }, None)
                th.unreadable.append(f"{name or 'image'}: image could not be decoded.")
                return
            # Only an EMBEDDED (cid) picture is a logo/stamp suspect. It is kept
            # when OCR finds real text in it (a pasted screenshot of a table);
            # dropped only when OCR ran and found next to nothing. If OCR cannot
            # run at all the picture is kept — "no reader" must never read as
            # "no text". A real (non-inline) attachment skips this test: it is
            # always read, whatever its size.
            if inline:
                from app.services.extraction.ocr import ocr_status, ocr_text
                if ocr_status() == "ready":
                    ocr = ocr_text([jpeg], payload, "image")
                    if len(ocr.strip()) < settings.ocr_min_chars:
                        _record_dropped(th, {
                            "name": name, "mime": ctype, "size": len(payload), "msg_index": msg_index,
                            "filter": "ocr",
                            "ocr_chars": len(ocr.strip()),
                            "reason": f"embedded image ({len(payload) // 1024}KB) with only "
                                      f"{len(ocr.strip())} char(s) of text - likely a logo/icon",
                            "thumb": _thumb_uri(jpeg),
                        }, jpeg)
                        return
            it.images = [jpeg]
            it.send_mode = "image (only option for a picture)"
        elif ext in (".xlsx", ".xlsm", ".xls") or ftype == "xlsx":
            _fill_office_item(it, payload, "xlsx", ext or ".xlsx")
        elif ext == ".csv":
            it.text = scrub_text(payload.decode("utf-8", "replace"))
            it.send_mode = "native"
        elif ext == ".docx" or ftype == "docx":
            _fill_office_item(it, payload, "docx", ext or ".docx")
        elif ext == ".txt":
            it.text = scrub_text(payload.decode("utf-8", "replace"))
            it.send_mode = "native"
        else:
            it.text = f"[unsupported attachment type {ctype or ftype or 'unknown'}]"
        if not it.images and not it.text.strip():
            # Nothing at all could be rendered or read from this attachment
            # (a PDF that would not open, an office file LibreOffice choked on).
            th.unreadable.append(f"{it.name}: no pages or text could be read from this file.")
        th.items.append(it)

    def walk(msg, depth: int) -> None:
        msg_index = len(th.messages)
        m = Message(index=msg_index, depth=depth,
                    subject=str(msg.get("Subject") or ""),
                    frm=scrub_text(str(msg.get("From") or "")),
                    to=scrub_text(str(msg.get("To") or "")),
                    date=str(msg.get("Date") or ""))
        th.messages.append(m)
        plain: list[str] = []
        html_parts: list[str] = []

        def handle(part) -> None:
            ctype = part.get_content_type()
            disp = part.get_content_disposition()
            if ctype == "message/rfc822":
                payload = part.get_payload()
                sub = payload[0] if isinstance(payload, list) and payload else payload
                if sub is not None and depth + 1 > _max_depth():
                    th.unreadable.append(
                        f"forwarded message nested {depth + 1} levels deep — not opened.")
                if sub is not None and depth + 1 <= _max_depth():
                    # Outlook lists these as named .eml attachments — record
                    # the outer filename for the Extracted badge.
                    fname = (part.get_filename() or "").replace("\x00", "") or None
                    if fname:
                        try:
                            raw = sub.as_bytes() if hasattr(sub, "as_bytes") else b""
                        except Exception:
                            raw = b""
                        th.opened_containers.append({
                            "name": fname,
                            "digest": _content_digest(raw or fname.encode()),
                        })
                    walk(sub, depth + 1)
                return
            if part.is_multipart():
                for p in part.iter_parts():
                    handle(p)
                return
            if ctype == "text/plain" and disp != "attachment":
                try:
                    plain.append(part.get_content())
                except Exception:
                    pass
                return
            if ctype == "text/html" and disp != "attachment":
                try:
                    html_parts.append(part.get_content())
                except Exception:
                    pass
                return
            fname = part.get_filename() or "unnamed"
            try:
                payload = part.get_payload(decode=True) or b""
            except Exception:
                payload = b""
            is_inline = (disp == "inline") or bool(part.get("Content-Id"))
            add_attachment(fname, payload, ctype, msg_index, is_inline, depth)

        handle(msg)
        # Conversation text is always plain text, never rendered to an image.
        # A pasted timesheet grid usually lives in the HTML alternative, not
        # the plain one — prefer whichever carries more content.
        body = "\n".join(plain).strip()
        html_flat = _html_to_text("\n".join(html_parts)) if html_parts else ""
        if html_flat and len(html_flat.strip()) > len(body):
            body = html_flat
        m.body = scrub_text(body)

    for _label, eml_bytes in messages:
        try:
            root = _email.message_from_bytes(eml_bytes, policy=_email.policy.default)
        except Exception:
            th.unreadable.append(f"message '{_label}': could not be parsed.")
            continue
        walk(root, 0)

    # Every message's body becomes its own item too, so pass 1 can classify a
    # pasted grid or a pasted approval note the same as any attachment — as
    # plain text, never as an image.
    for m in th.messages:
        if m.body.strip():
            th.items.append(Item(key=new_key(), name=f"email body (message {m.index + 1})",
                                 mime="text/plain", msg_index=m.index, text=m.body))
    # Collapse byte-identical re-attachments before pass 1. Look-alike copies
    # (e.g. one signed, one not) are DIFFERENT files and are all kept.
    _dedupe_sheet_items(th)
    return th


def require_vision_configured() -> tuple[str, str]:
    """(api_key, model) — raises a clear error if no vision model is set up.

    There is no fallback pipeline: extraction only runs against the
    configured vision model."""
    from app.services.extraction import vision_client

    api_key = vision_client.openai_api_key()
    if not api_key or api_key.lower() == "change-me":
        raise RuntimeError(
            "AI extraction is not configured — set OPENAI_API_KEY (and LLM_PROVIDER) "
            "in the environment before running Extract Email or Upload.")
    model = vision_client.model_for(vision_client.vision_provider(), "vision")
    return api_key, model


_KINDS = ("timesheet", "leave_certificate", "approval", "other")


def _normalise_pass1_items(raw_items: list) -> list[dict]:
    out: list[dict] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "other").lower()
        if kind not in _KINDS:
            kind = "other"
        out.append({
            "source": str(it.get("source") or "").strip() or "(unknown)",
            "kind": kind,
            "employee_name": (str(it.get("employee_name")).strip() or None)
            if it.get("employee_name") else None,
            "employee_id": (str(it.get("employee_id")).strip() or None)
            if it.get("employee_id") else None,
            "covers_multiple_employees": bool(it.get("covers_multiple_employees")),
            "employee_row_count": (int(it["employee_row_count"])
                                   if isinstance(it.get("employee_row_count"), (int, float)) else None),
            "period_hint": str(it.get("period_hint") or "").strip(),
            "evidence": str(it.get("evidence") or "").strip(),
            "manager_signature": bool(it.get("manager_signature")),
            "signature_evidence": str(it.get("signature_evidence") or "").strip(),
            "signature_is_named_only": bool(it.get("signature_is_named_only")),
            "notes": str(it.get("notes") or "").strip(),
        })
    return out


def _derive_approval(triage: list[dict]) -> dict:
    """Approval is derived from the model's own per-item verdicts, not
    re-detected separately: either an item IS approval evidence
    (kind == "approval"), or a timesheet/leave_certificate carries its own
    approval signal (manager_signature).

    A name typed next to "Approved by:" with no signature/stamp/status mark
    (signature_is_named_only) is NOT enough on its own to detect approval -
    anyone can type a name into a field. It still gets recorded and surfaced
    to the reviewer (weak_evidence + a note naming what was found), but only
    a real signal (a stamp, a chain of approvers, a manager's own reply)
    flips `detected`. A genuinely strong signal elsewhere in the same thread
    still wins regardless of a weak one also being present."""
    approval = {"detected": False, "evidence": "", "source": "", "where": "none", "weak_evidence": False}
    rank = {"sheet": 2, "conversation": 1, "none": 0}
    weak_detail = ""
    for m in triage:
        sig = bool(m.get("manager_signature"))
        ev = m.get("signature_evidence") or m.get("evidence") or ""
        if sig and m.get("signature_is_named_only"):
            if not approval["weak_evidence"]:
                approval["weak_evidence"] = True
                weak_detail = (
                    f"Only a name was found ({ev[:200] or 'no detail given'}) near an "
                    f"approval field on {m.get('source')} - no signature, stamp, or "
                    "status mark. Needs manual verification.")
            continue
        if m.get("kind") == "approval" or sig:
            # An approval REPLY / standalone approval item is conversation-level even
            # when the model also set manager_signature on it (the prompt tells it
            # to); only a sheet's OWN signature/stamp is sheet-level. Otherwise, once a
            # second employee's sheet joined the thread, a manager's "Approved." was
            # re-labelled "on another employee's sheet" and dropped from everyone.
            where = "conversation" if m.get("kind") == "approval" else ("sheet" if sig else "conversation")
            if rank[where] >= rank[approval["where"]]:
                approval = {**approval, "detected": True, "evidence": ev,
                           "source": m.get("source"), "where": where}
    approval["detail"] = (
        f"Approval found: {approval['evidence'][:200]}" if approval["detected"] and approval["evidence"]
        else "Approval found." if approval["detected"]
        else weak_detail if approval["weak_evidence"]
        else "No manager approval found in this thread.")
    return approval


def _build_summary(triage: list[dict], approval: dict, thread_summary: str, n_batches: int) -> dict:
    n_ts = sum(1 for m in triage if m["kind"] == "timesheet")
    n_cert = sum(1 for m in triage if m["kind"] == "leave_certificate")
    if n_ts + n_cert == 0:
        headline, status = "No timesheet or leave evidence identified.", "other"
    elif approval["detected"]:
        headline, status = f"{n_ts} timesheet(s), {n_cert} leave record(s) — approval detected.", "approved"
    else:
        headline, status = (f"{n_ts} timesheet(s), {n_cert} leave record(s) — "
                            f"no approval detected yet."), "awaiting_approval"
    # `narrative` is what ThreadSummaryBox shows as the prose body — use the
    # model's thread_summary (2–4 sentences). Call-count stays as a footnote.
    prose = (thread_summary or "").strip() or (
        f"Pass 1 ({n_batches} call(s)): {len(triage)} item(s) reviewed.")
    return {
        "headline": headline,
        "status": status,
        "thread_summary": prose,
        "narrative": prose,
        "pass1_note": f"Pass 1 ({n_batches} call(s)): {len(triage)} item(s) reviewed.",
        "action_needed": "Spot-check any 'other' items and any verdict you disagree with.",
    }


def _enrich_thread_summary(
    summary_obj: dict, *, triage: list[dict], approval: dict,
    message_count: int, model: str,
) -> dict:
    """Full ThreadSummary shape the Inbox / Extract UI both render."""
    from datetime import datetime, timezone

    data_items = [t for t in triage if t.get("kind") in ("timesheet", "leave_certificate")]
    employees = [t.get("employee_name") for t in data_items if t.get("employee_name")]
    periods = [t.get("period_hint") for t in data_items if t.get("period_hint")]
    return {
        **summary_obj,
        "timesheet_sent": bool(data_items),
        "approval_requested": summary_obj.get("status") == "awaiting_approval",
        "approval_given": bool(approval.get("detected")),
        "employee": ", ".join(dict.fromkeys(e for e in employees if e)),
        "period": next(iter(dict.fromkeys(p for p in periods if p)), ""),
        "message_count": message_count,
        "model": model,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def _ui_pass_item(t: dict, items: list[Item]) -> dict:
    it = resolve_source(t.get("source") or "", items)
    return {
        # "source" stays the raw [A#] label (matches the model's own JSON,
        # useful for debugging); "name" is the real attachment/body name for
        # the live Extract UI to show instead — "[A11]" means nothing to a
        # reviewer watching the run happen.
        "source": t["source"], "name": it.name if it is not None else t["source"],
        "kind": t["kind"],
        "employee": t.get("employee_name"), "employee_id": t.get("employee_id"),
        "covers_multiple_employees": bool(t.get("covers_multiple_employees")),
        "employee_row_count": t.get("employee_row_count"),
        "period": t.get("period_hint"),
        "signature": bool(t.get("manager_signature")),
        "notes": t.get("notes") or "",
        "thumb": _item_thumb(it) if it is not None else None,
        "key": it.key if it is not None else None,
    }


def _safe_int(v, default: int = 0) -> int:
    """int() that never raises on a model's "12 days" / "n/a" / None."""
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return int(v)
    m = re.search(r"-?\d+", str(v or ""))
    return int(m.group()) if m else default


def _normalise_pass2_sheets(raw_sheets: list, triage: list[dict], items: list[Item]) -> list[dict]:
    """Pass-2 JSON -> the sheet shape grouping.py consumes. Identity/kind come
    from pass 1 (whole-conversation context); the day-by-day account comes
    from pass 2 (which read the item closely). Where pass 2 reports a
    different printed name, it wins — it was looking straight at the header.
    """
    by_source = {t["source"]: t for t in triage}
    sheets: list[dict] = []
    for s in raw_sheets:
        if not isinstance(s, dict):
            continue
        source = str(s.get("source") or "").strip() or "(unknown)"
        t = by_source.get(source)
        if t is None:
            it = resolve_source(source, items)
            if it is not None:
                t = next((tt for tt in triage if resolve_source(tt["source"], items) is it), {})
            else:
                t = {}

        try:
            month = int(s.get("month")) if s.get("month") else None
            month = month if month and 1 <= month <= 12 else None
        except (TypeError, ValueError):
            month = None
        try:
            year = int(s.get("year")) if s.get("year") else None
            year = year if year and 2000 <= year <= 2100 else None
        except (TypeError, ValueError):
            year = None

        it = resolve_source(source, items)
        sheets.append({
            # The real attachment/body name for humans (Inbox, Compare & Fix,
            # missing/uncertain-day flags, the debug view) — "[A5]" meant
            # nothing to a reviewer. `_source_key` keeps the raw [A#] label
            # so this run's OWN resolve_source(..., th.items) calls (see
            # `_fresh_by_digest` below) still match by key, not by a name
            # that might not be unique (two attachments both called
            # "image.png") or stable across a later run's fresh numbering.
            "name": it.name if it is not None else source,
            # Content hash of the file this sheet was read from: the stable identity
            # of one copy when an employee sends look-alike copies (grouping.py).
            "digest": it.digest if it is not None else "",
            "_source_key": source,
            "kind": t.get("kind", "timesheet"),
            "employee_name": (str(s.get("employee_name")).strip() or None)
            if s.get("employee_name") else t.get("employee_name"),
            "employee_id": (str(s.get("employee_id")).strip() or None)
            if s.get("employee_id") else t.get("employee_id"),
            "month": month,
            "year": year,
            "days_covered": max(0, _safe_int(s.get("days_covered"))),
            "period_type": str(s.get("period_type") or "unknown").lower()
            if str(s.get("period_type") or "").lower() in
               ("full_month", "half_month", "week", "partial", "unknown") else "unknown",
            "missing_days": [_safe_int(d) for d in (s.get("missing_days") or [])
                            if str(d).strip().lstrip("-").isdigit()
                            and 1 <= _safe_int(d) <= (
                                calendar.monthrange(year, month)[1]
                                if month and year else 31)],
            "working_days": s.get("working_days") or [],
            "weekend_days": s.get("weekend_days") or [],
            "uncertain_days": s.get("uncertain_days") or [],
            "annual": s.get("annual") or [],
            "remote": s.get("remote") or [],
            "sick": s.get("sick") or [],
            "maternity": s.get("maternity") or [],
            "unpaid": s.get("unpaid") or [],
            "absent": s.get("absent") or [],
            "public_holiday": s.get("public_holiday") or [],
            "other": s.get("other") or [],
            "manager_signature": bool(t.get("manager_signature")),
            "approval_evidence": t.get("signature_evidence", ""),
            "approval_named_only": bool(t.get("signature_is_named_only")),
            "text": it.text if it is not None else "",
            "notes": " ".join(x for x in (t.get("notes", ""), str(s.get("notes") or "")) if x).strip(),
        })
    return sheets


def _synthetic_triage_from_sheet(s: dict) -> dict:
    """A reused (cached) sheet's own fields, shaped like a pass-1 triage entry
    — so approval/summary logic can treat "already known from a past run" the
    same as "freshly classified this run", without spending a vision call to
    re-confirm what a past run already established."""
    return {
        "source": s.get("name", ""),
        "kind": s.get("kind", "other"),
        "employee_name": s.get("employee_name"),
        "employee_id": s.get("employee_id"),
        "period_hint": "",
        "evidence": "",
        "manager_signature": bool(s.get("manager_signature")),
        "signature_evidence": s.get("approval_evidence", ""),
        "signature_is_named_only": bool(s.get("approval_named_only")),
        "notes": "",
    }


async def extract_thread_sheets(
    messages: list[tuple[str, bytes]],
    *, cached_sheets: dict[str, dict] | None = None,
    calendars: dict[tuple[int, int], dict] | None = None,
    fresh_window: bool = False,
) -> tuple[list[dict], dict, list[dict], dict]:
    """Two passes over one conversation. See the module docstring.

    `cached_sheets` (digest -> stored entry, see sheet_cache.thread_cached_sheets)
    is what makes an incremental window (new + a couple of context messages,
    see thread_collect.collect_thread_emls' `since`) safe to use: an item in
    this run's window whose digest is already cached skips vision entirely —
    its stored sheet is reused as-is. Anything cached for a message OUTSIDE
    this window (never fetched at all this run) is merged in too, so the
    result is always a complete answer for the whole conversation, never just
    "whatever this run happened to look at." `cached_sheets=None`/`{}` (the
    default) is exactly today's behaviour — everything gets a fresh read.

    `calendars` (keyed by (month, year), see admin's /admin/calendars) is
    handed to Pass 2 as ground truth for that item's weekend dates + public
    holidays instead of the model inferring them — see
    thread_prompt._calendar_block. Kept as a plain dict handed in (like
    `cached_sheets`) rather than queried here, so this function stays
    DB-free. `None`/`{}` (the default) is a pure no-op fallback to today's
    self-inferred behaviour.

    Returns (sheets, approval, conflicts, run_meta). `conflicts` is always []
    — cross-sheet duplicate/complementary detection lives in grouping.py,
    which sees every sheet already grouped by employee+month.
    `run_meta["summary_obj"]` carries pass 1's conversation summary.
    """
    from app.services.extract_email import thread_prompt, triage_prompt

    emit("unpack", "spin", "Collecting the whole thread…")
    th = collect_thread(messages)
    emit("unpack", "ok",
         f"{len(th.items)} item(s), {th.n_images} image(s) from {len(messages)} message(s)"
         + (f", {len(th.dropped)} auto-dropped" if th.dropped else "") + ".",
         items=[{"key": it.key, "name": it.name, "mime": it.mime,
                 "size": it.size, "send_mode": it.send_mode,
                 "thumb": _item_thumb(it)} for it in th.items],
         dropped=[{"name": d.get("name"), "reason": d.get("reason"),
                   "size": d.get("size"), "mime": d.get("mime"),
                   "filter": d.get("filter") or (
                       "ocr" if "OCR" in str(d.get("reason") or "") else "size"),
                   "ocr_chars": d.get("ocr_chars"),
                   "kept_key": d.get("kept_key"),
                   "kept_name": d.get("kept_name"),
                   "thumb": d.get("thumb")} for d in th.dropped])

    # ---- reuse whatever this thread already had extracted -------------------
    cached_sheets = cached_sheets or {}
    all_window_digests = {it.digest for it in th.items if it.digest}
    window_cached_sheets: list[dict] = []
    if cached_sheets:
        fresh_items = []
        for it in th.items:
            # fresh_window: the reviewer asked for exactly these messages to be read
            # again, so nothing in this run's window is served from the record.
            entry = cached_sheets.get(it.digest) if (it.digest and not fresh_window) else None
            # badge_only entries mark nested .eml containers — never sheets.
            if (entry is not None and entry.get("sheet")
                    and not entry.get("badge_only")):
                window_cached_sheets.append(entry["sheet"])
            else:
                fresh_items.append(it)
        th.items = fresh_items
    external_sheets = [
        entry["sheet"] for digest, entry in cached_sheets.items()
        if digest not in all_window_digests
        and entry.get("sheet") and not entry.get("badge_only")
    ]
    reused_sheets = window_cached_sheets + external_sheets
    if reused_sheets:
        emit("unpack", "ok",
             f"{len(reused_sheets)} sheet(s) reused from a previous read — not re-sent to the model.",
             reused=[{"name": s.get("name"), "employee": s.get("employee_name")} for s in reused_sheets])
    reused_triage = [_synthetic_triage_from_sheet(s) for s in reused_sheets]

    empty_meta = {
        "method": "thread-two-pass", "model": None, "calls": 0,
        "sheet_count": len(reused_sheets), "errors": [], "skipped": th.dropped,
        "warnings": list(th.unreadable), "incomplete": False,
        "conflicts": [], "summary": "", "summary_obj": None,
        "_opened_containers": list(th.opened_containers),
    }
    if not th.items and not any(m.body.strip() for m in th.messages):
        if reused_sheets:
            approval = _derive_approval(reused_triage)
            return reused_sheets, approval, [], {
                **empty_meta, "reused_sheets": len(reused_sheets),
                "summary": "Nothing new in this thread — showing what was already extracted."}
        return [], {"detected": False, "detail": "Nothing readable in this thread."}, [], {
            **empty_meta, "errors": ["empty thread"]}

    api_key, model = require_vision_configured()

    # ---- PASS 1: classify every item ---------------------------------------
    batches = triage_prompt.chunk_items_by_count(
        th.items, settings.pass1_batch_size, max_images=settings.max_images_per_call)
    emit("pass1", "spin",
         f"Pass 1 of 2 — {model} is reading the whole conversation "
         f"({len(batches)} call(s))…",
         pass_no=1, model=model, message_count=len(messages))

    # RETRIABLE problems of this run (a model call that ultimately failed, an
    # item the model never answered for). Any entry marks the run `incomplete`
    # so it is retried, never marked "checked" (see meta["incomplete"] below).
    # Permanent problems (a corrupt attachment) are `warnings` instead.
    batch_errors: list[str] = []
    pass1_calls = 0
    thread_summary = ""

    def _reply_keys(reply: list) -> set[str]:
        keys: set[str] = set()
        for m in reply:
            it = resolve_source(str(m.get("source") or ""), th.items)
            if it is not None:
                keys.add(it.key)
        return keys

    async def _pass1_call(batch: list, note: str, label: str, detail: str | None = None) -> dict:
        nonlocal pass1_calls
        data = await triage_prompt.run_pass1_batch(
            th, batch, note, model, api_key, label=label, detail=detail)
        count_llm()
        pass1_calls += 1
        return data

    async def _pass1_resilient(batch: list, note: str, label: str, bi: int) -> list:
        """Verdicts for `batch`. A failed call is SPLIT and retried (down to one
        item) instead of losing every item in it; an item the model simply did
        not answer for is asked about again on its own. Whatever still cannot
        be classified is recorded in batch_errors — never dropped silently."""
        nonlocal thread_summary
        try:
            data = await _pass1_call(batch, note, label)
        except Exception as e:
            if len(batch) > 1:
                emit("pass1", "warn",
                     f"Pass 1 batch {bi} failed with {len(batch)} items ({str(e)[:100]}) - "
                     f"splitting it in two and retrying.",
                     pass_no=1, model=model, batch_index=bi, batch_total=len(batches),
                     batch_phase="retry")
                mid = len(batch) // 2
                out: list = []
                for part, suffix in ((batch[:mid], "a"), (batch[mid:], "b")):
                    out += await _pass1_resilient(part, note, f"{label}{suffix}", bi)
                return out
            msg = f"Pass 1: '{batch[0].name}' could not be classified ({str(e)[:140]})."
            batch_errors.append(msg)
            emit("pass1", "warn", msg, pass_no=1, model=model, batch_index=bi,
                 batch_total=len(batches), batch_phase="failed")
            return []
        if not thread_summary:
            thread_summary = (data.get("thread_summary") or "").strip()
        reply = [m for m in (data.get("items") or []) if isinstance(m, dict)]
        got = _reply_keys(reply)
        for it in [x for x in batch if x.key not in got]:
            emit("pass1", "warn",
                 f"Pass 1 gave no verdict for '{it.name}' - asking about it on its own…",
                 pass_no=1, model=model, batch_index=bi, batch_total=len(batches),
                 batch_phase="retry")
            try:
                d2 = await _pass1_call([it], note, f"{label}-{it.key}")
            except Exception as e:
                batch_errors.append(
                    f"Pass 1: '{it.name}' could not be classified ({str(e)[:140]}).")
                continue
            r2 = [m for m in (d2.get("items") or []) if isinstance(m, dict)]
            if it.key in _reply_keys(r2):
                reply += r2
            else:
                batch_errors.append(f"Pass 1: the model returned no verdict for '{it.name}'.")
        return reply

    all_items: list = []
    for bi, batch in enumerate(batches, 1):
        note = (f"This is batch {bi} of {len(batches)}. You are seeing SOME of the thread's "
                f"items below; classify ONLY those. Other batches cover the rest, but all "
                f"message bodies are included so you have full approval context."
                if len(batches) > 1 else "")
        # Sent -> received bracket around the actual network call, so the live
        # UI can show "waiting for a response" instead of a step that looks
        # frozen — and, with more than one batch, exactly which one is in flight.
        emit("pass1", "spin",
             f"Pass 1 — sending batch {bi} of {len(batches)} ({len(batch)} item(s)) to {model}…",
             pass_no=1, model=model, batch_index=bi, batch_total=len(batches), batch_phase="sent")
        all_items += await _pass1_resilient(batch, note, f"pass1-batch{bi}", bi)
        emit("pass1", "spin",
             f"Pass 1 — batch {bi} of {len(batches)} received.",
             pass_no=1, model=model, batch_index=bi, batch_total=len(batches), batch_phase="received")

    # One verdict per item. If the same item is judged twice (a model echoing
    # another batch's label), a timesheet/certificate/approval verdict is never
    # overwritten by "other"/"noise" — losing a sheet is the costlier mistake.
    _data_kinds = ("timesheet", "leave_certificate", "approval")
    verdicts: dict[str, dict] = {}
    unresolved: list[dict] = []
    for m in all_items:
        it = resolve_source(str(m.get("source") or ""), th.items)
        if it is None:
            unresolved.append(m)
            continue
        prev = verdicts.get(it.key)
        if prev is None or (str(prev.get("kind") or "").lower() not in _data_kinds
                            and str(m.get("kind") or "").lower() in _data_kinds):
            verdicts[it.key] = m

    # ---- second look at everything filed as other/noise that has pictures ----
    # Pass 1 is a quick triage over several items at once; a dense screenshot of
    # a weekly timesheet can be waved through as "other". Every picture-bearing
    # item (never a message body) that came back other/noise is shown AGAIN,
    # alone, at full detail, with an explicit prompt to look harder. A positive
    # verdict from the second look wins; an unchanged one stands.
    if settings.pass1_rescue_other:
        rescue = [it for it in th.items
                  if _wants_second_look(it)
                  and str((verdicts.get(it.key) or {}).get("kind") or "").lower()
                  not in _data_kinds
                  and it.key in verdicts]
        for it in rescue:
            note = ("SECOND LOOK. A quick first pass filed this item as 'other'/'noise'. Look at "
                    "it again in full before agreeing: a screenshot, photo, scan or spreadsheet of "
                    "a timesheet / attendance table, a leave record or certificate, an email that "
                    "states leave dates (e.g. 'I will be on sick leave from 3 to 5 June'), or an "
                    "approval message must NOT be filed as other. Keep 'other' only if it is "
                    "genuinely unrelated (logo, banner, signature block, advertisement, invoice, "
                    "ordinary conversation).")
            try:
                d2 = await _pass1_call([it], note, f"pass1-rescue-{it.key}",
                                       detail=settings.pass1_rescue_detail)
            except Exception as e:
                batch_errors.append(
                    f"Pass 1 second look: '{it.name}' could not be re-checked ({str(e)[:120]}).")
                continue
            r2 = [m for m in (d2.get("items") or []) if isinstance(m, dict)]
            for m in r2:
                hit = resolve_source(str(m.get("source") or ""), th.items)
                if (hit is not None and hit.key == it.key
                        and str(m.get("kind") or "").lower() in _data_kinds):
                    verdicts[it.key] = m
                    emit("pass1", "warn",
                         f"Second look reclassified '{it.name}' as {m.get('kind')}.",
                         pass_no=1, model=model)
                    break

    final_items = list(verdicts.values()) + unresolved

    def _noise_label(source: str) -> str:
        it = resolve_source(source, th.items)
        return it.name if it is not None else source

    noise = [_noise_label(str(m.get("source"))) for m in final_items
             if (m.get("kind") or "").lower() == "noise"]
    triage = _normalise_pass1_items(
        [m for m in final_items if (m.get("kind") or "").lower() != "noise"])

    # Approval/summary reason over fresh AND reused sheets together, so a
    # signature found on a message outside this run's window (or already
    # confirmed on a cached item inside it) is never forgotten just because
    # this run didn't re-read it.
    combined_triage = triage + reused_triage
    approval = _derive_approval(combined_triage)
    summary_obj = _build_summary(combined_triage, approval, thread_summary, len(batches))
    confirmed_kind = [t for t in triage if t["kind"] in ("timesheet", "leave_certificate")]
    # A multi-employee roster (one grid, many people) is a fundamentally
    # different shape than the per-person sheets pass 2 is built to read —
    # extracting it here would pick one row and silently drop everyone else.
    # Pass 1 already flagged it (triage_prompt.py); route it away from pass 2
    # rather than let it through as an ordinary timesheet. It is surfaced as a
    # persistent WARNING on every item staged from this thread.
    multi_employee_items = [t for t in confirmed_kind if t.get("covers_multiple_employees")]
    data_items = [t for t in confirmed_kind if not t.get("covers_multiple_employees")]
    multi_employee_labels: list[str] = []
    for t in multi_employee_items:
        it = resolve_source(t.get("source"), th.items)
        label = it.name if it is not None else t.get("source")
        count = t.get("employee_row_count")
        multi_employee_labels.append(f"{label} (~{count} employees)" if count else str(label))
    full_summary = _enrich_thread_summary(
        summary_obj, triage=combined_triage, approval=approval,
        message_count=len(messages), model=model)

    emit("pass1", "ok",
         f"Pass 1 done — {len(data_items)} timesheet/certificate(s) identified"
         + (f", {len(noise)} noise item(s) ignored" if noise else "")
         + (f", {len(multi_employee_labels)} multi-employee roster(s) skipped (use Bulk Upload)"
            if multi_employee_labels else "") + ".",
         pass_no=1,
         items=[_ui_pass_item(t, th.items) for t in triage],
         confirmed=[_ui_pass_item(t, th.items) for t in data_items],
         employees=[t.get("employee_name") for t in data_items if t.get("employee_name")],
         noise=noise, approval=approval,
         summary=full_summary,
         thread_summary=full_summary)

    warnings: list[str] = list(th.unreadable)
    if multi_employee_labels:
        warnings.append(
            "Not extracted — looks like a multi-employee roster (upload it on the Bulk Upload "
            "page): " + ", ".join(multi_employee_labels))

    # ---- copies of ONE timesheet: read one, keep them all (see alternates.py) ----
    from app.services.extract_email import alternates as alternates_mod
    alt = await alternates_mod.resolve(
        th, data_items, model, api_key, resolve_source=resolve_source, emit=emit)
    if alt.calls:
        for _ in range(alt.calls):
            count_llm()
    if alt.skip_keys:
        def _is_skipped(t: dict) -> bool:
            it = resolve_source(t.get("source"), th.items)
            return it is not None and it.key in alt.skip_keys
        data_items = [t for t in data_items if not _is_skipped(t)]
    warnings.extend(alt.warnings)

    meta = {
        "method": "thread-two-pass", "model": model, "calls": pass1_calls,
        "sheet_count": 0, "errors": batch_errors, "warnings": warnings,
        "incomplete": bool(batch_errors),
        "skipped": th.dropped, "conflicts": [],
        "noise": noise, "summary": full_summary.get("headline", ""),
        "summary_obj": full_summary, "triage": triage,
        "thread_summary": full_summary,
        "_opened_containers": list(th.opened_containers),
        "multi_employee_skipped": multi_employee_labels,
        "alternates": alt.records,
    }

    def _finish_meta() -> None:
        meta["incomplete"] = bool(batch_errors)
        meta["warnings"] = warnings

    if not data_items:
        _finish_meta()
        if reused_sheets:
            meta["sheet_count"] = len(reused_sheets)
            meta["reused_sheets"] = len(reused_sheets)
            return reused_sheets, approval, [], meta
        return [], approval, [], meta

    # ---- PASS 2: read only the confirmed items -----------------------------
    pairs = thread_prompt.confirmed_sheets(data_items, th.items)
    if not pairs:
        batch_errors.append("pass 1 confirmed sheets that could not be resolved to any item")
        emit("extract", "warn", batch_errors[-1])
        _finish_meta()
        if reused_sheets:
            meta["sheet_count"] = len(reused_sheets)
            meta["reused_sheets"] = len(reused_sheets)
            return reused_sheets, approval, [], meta
        return [], approval, [], meta

    def _make_batches(pair_list: list) -> list[list]:
        out: list[list] = []
        cur: list = []
        n = 0
        # Bound a call by SHEETS as well as images. Each sheet's reply lists every
        # date of a month, so ~12 one-image sheets in one call (14 weekly/monthly
        # reports in one email, say) overran the model's output limit, came back
        # as cut-off JSON, and the whole batch was lost. Image count alone can't
        # see that.
        max_sheets = max(int(getattr(settings, "pass2_max_sheets_per_call", 0) or 0), 0)
        for pair in pair_list:
            k = max(len(pair[0].images), 1)
            if cur and (n + k > settings.max_images_per_call
                        or (max_sheets and len(cur) >= max_sheets)):
                out.append(cur)
                cur, n = [], 0
            cur.append(pair)
            n += k
        if cur:
            out.append(cur)
        return out

    batches2 = _make_batches(pairs)

    needs_body_context = any(it.name.startswith("email body") for it, _ in pairs)
    body_text = ""
    if needs_body_context:
        body_text = "\n".join(b for _m, b in triage_prompt.budgeted_bodies(th))

    emit("pass2", "spin",
         f"Pass 2 of 2 — extracting {len(pairs)} confirmed sheet(s) ({len(batches2)} call(s))…",
         pass_no=2, model=model, sheets=[it.name for it, _ in pairs])

    raw_sheets: list = []
    pass2_call_count = 0
    failed_keys: set[str] = set()      # items already reported as failed - not re-tried below

    async def _pass2_adaptive(batch: list, label: str, bi: int) -> dict:
        """One Pass 2 call; if it fails (incl. a reply cut off at the output
        limit) and holds more than one sheet, split it in half and retry each
        half (down to a single sheet) instead of losing the whole batch. A
        single sheet that still fails raises and is recorded by the caller.
        Returns {"sheets": [...]}."""
        nonlocal pass2_call_count
        try:
            data = await thread_prompt.run_pass2_batch(
                batch, body_text, model, api_key, calendars=calendars, label=label)
            count_llm()
            pass2_call_count += 1
            return data
        except Exception as e:
            if len(batch) <= 1:
                raise
            emit("pass2", "warn",
                 f"Pass 2 batch {bi} failed with {len(batch)} sheets ({str(e)[:100]}) - "
                 f"splitting it in two and retrying.",
                 pass_no=2, model=model, batch_index=bi, batch_total=len(batches2), batch_phase="retry")
            mid = len(batch) // 2
            merged: list = []
            for part, suffix in ((batch[:mid], "a"), (batch[mid:], "b")):
                try:
                    merged += (await _pass2_adaptive(part, f"{label}{suffix}", bi)).get("sheets") or []
                except Exception as e2:
                    failed_keys.update(it.key for it, _ in part)
                    names = ", ".join(it.name for it, _ in part)
                    batch_errors.append(
                        f"Pass 2: could not read {names} even after splitting ({str(e2)[:120]}).")
            return {"sheets": merged}

    for bi, batch in enumerate(batches2, 1):
        # Same sent -> received bracket as pass 1.
        emit("pass2", "spin",
             f"Pass 2 — sending batch {bi} of {len(batches2)} ({len(batch)} sheet(s)) to {model}…",
             pass_no=2, model=model, batch_index=bi, batch_total=len(batches2), batch_phase="sent")
        # One batch's call ultimately failing must not discard every OTHER
        # batch's already-confirmed sheets. The failure is recorded (marking
        # the run incomplete) and its sheets are picked up by the retry.
        try:
            data = await _pass2_adaptive(batch, f"pass2-batch{bi}", bi)
        except Exception as e:
            failed_keys.update(it.key for it, _ in batch)
            names = ", ".join(it.name for it, _ in batch)
            msg = (f"Pass 2 batch {bi}/{len(batches2)} failed ({str(e)[:160]}) — "
                   f"not extracted this run: {names}.")
            batch_errors.append(msg)
            emit("pass2", "warn", msg, pass_no=2, model=model,
                 batch_index=bi, batch_total=len(batches2), batch_phase="failed")
            continue
        emit("pass2", "spin",
             f"Pass 2 — batch {bi} of {len(batches2)} received.",
             pass_no=2, model=model, batch_index=bi, batch_total=len(batches2), batch_phase="received")
        raw_sheets += [s for s in (data.get("sheets") or []) if isinstance(s, dict)]

    # ---- coverage check: every confirmed item must have produced a sheet ----
    # Pass 1 read these same images successfully and called them timesheets /
    # certificates, so an item missing from the reply is a dropped item (the
    # model skipped one of several, or returned nothing), not a blank document.
    # Re-ask for each one on its own; whatever still has no sheet is recorded.
    def _covered_keys(sheet_list: list) -> set[str]:
        out: set[str] = set()
        for sh in sheet_list:
            hit = resolve_source(str(sh.get("source") or ""), th.items)
            if hit is not None:
                out.add(hit.key)
        return out

    covered = _covered_keys(raw_sheets)
    # Items already reported as a Pass 2 failure are not re-tried here.
    for pair in [p for p in pairs if p[0].key not in covered and p[0].key not in failed_keys]:
        it = pair[0]
        emit("pass2", "spin",
             f"Pass 2 returned no sheet for '{it.name}' — reading it again on its own…",
             pass_no=2, model=model, batch_phase="retry")
        try:
            d2 = await _pass2_adaptive([pair], f"pass2-recover-{it.key}", 0)
        except Exception as e:
            batch_errors.append(f"Pass 2: '{it.name}' could not be read ({str(e)[:140]}).")
            continue
        got = [s for s in (d2.get("sheets") or []) if isinstance(s, dict)]
        if it.key in _covered_keys(got):
            raw_sheets += got
        else:
            batch_errors.append(
                f"Pass 2: no timesheet data came back for '{it.name}' although it was "
                "identified as one.")

    fresh_sheets = _normalise_pass2_sheets(raw_sheets, data_items, th.items)
    meta["_fresh_by_digest"] = {
        it.digest: s for s in fresh_sheets
        if (it := resolve_source(s["_source_key"], th.items)) is not None and it.digest
    }
    # Nested .eml/.msg containers the Inbox lists as attachments — badge only.
    meta["_opened_containers"] = list(th.opened_containers)
    meta["reused_sheets"] = len(reused_sheets)
    sheets = fresh_sheets + reused_sheets
    total_days = sum(
        len(s["working_days"]) + len(s["weekend_days"])
        + sum(len(s[b]) for b in BUCKETS)
        for s in sheets)
    # UI gets the model JSON + normalised sheets (minus bulky OCR text).
    ui_sheets = [{k: v for k, v in s.items() if k != "text"} for s in sheets]
    emit("pass2", "ok" if not batch_errors else "warn",
         f"Pass 2 done — {len(sheets)} sheet(s), {total_days} day(s) accounted for"
         + (f"; {len(batch_errors)} problem(s) - run incomplete" if batch_errors else "") + ".",
         pass_no=2,
         raw={"sheets": raw_sheets},
         sheets=ui_sheets,
         results=[{"source": s["name"], "employee": s.get("employee_name"),
                   "employee_id": s.get("employee_id"), "month": s.get("month"), "year": s.get("year"),
                   "days_covered": s.get("days_covered"), "period_type": s.get("period_type"),
                   "leaves": {b: len(s.get(b) or []) for b in BUCKETS if s.get(b)},
                   "total_days": (len(s.get("working_days") or []) + len(s.get("weekend_days") or [])
                                  + sum(len(s.get(b) or []) for b in BUCKETS))}
                  for s in sheets])

    meta["calls"] = pass1_calls + pass2_call_count
    meta["sheet_count"] = len(sheets)
    _finish_meta()
    return sheets, approval, [], meta
