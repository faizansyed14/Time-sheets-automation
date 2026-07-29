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
import os
import re
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.pii import scrub_text
from app.services.extract_email.constants import BUCKETS
from app.services.extract_email.progress import count_llm, emit

# A forward of a forward of a forward is real; beyond this it is a mail loop,
# not a timesheet. Applies to .eml/.msg attached as a FILE (not MIME-nested —
# those are unwrapped for free by the normal MIME walk).
MAX_EML_DEPTH = 4
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
    file can't be parsed (corrupt / not really a .msg)."""
    import tempfile

    try:
        import extract_msg
    except ImportError:
        return None
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as f:
            f.write(payload)
            path = f.name
        msg = extract_msg.Message(path)
        try:
            return msg.export()
        finally:
            msg.close()
    except Exception:
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _pdf_pages(payload: bytes) -> tuple[list[bytes], str]:
    """Per-page JPEGs (one image per page, never stitched) + the PDF's own
    concatenated text layer, both capped at settings.pdf_max_pages."""
    import fitz

    from app.services.extraction.file_processor import pdf_to_page_images

    texts: list[str] = []
    try:
        doc = fitz.open(stream=payload, filetype="pdf")
        try:
            for i in range(min(doc.page_count, settings.pdf_max_pages)):
                texts.append(doc.load_page(i).get_text("text") or "")
        finally:
            doc.close()
    except Exception:
        texts = []
    try:
        images = pdf_to_page_images(payload, dpi=settings.pdf_render_dpi, max_pages=settings.pdf_max_pages)
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
        return pdf_to_page_images(pdf, dpi=settings.pdf_render_dpi, max_pages=settings.pdf_max_pages)
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


# A whole "Approved By: <name>" / "Approval Date: <date>" line (either word
# order) is approval metadata end-to-end — strip the label AND its value, or
# the approver's name/date leaks through as content the unsigned copy never
# had. Matched and stripped before the standalone-word pass below.
_APPROVAL_LINE_RE = re.compile(
    r"(?i)\b(?:approved\s*by|approval\s*date|date\s*(?:of\s*)?approval)\s*:?\s*[^\n]*"
)
# Standalone tokens that flip between an unsigned timesheet and the same
# sheet after manager approval / regenerate — strip before near-dup compare.
# Includes generic workflow-status words (status/submitted/pending/present)
# since those are exactly what differs between "awaiting sign-off" and
# "signed" copies of one sheet, the same way approved/signed/manager are.
_APPROVAL_NOISE_RE = re.compile(
    r"(?i)\b("
    r"approval|approved|approver|"
    r"signature|signed|signatory|stamp|"
    r"line\s*manager|manager|\blm\b|"
    r"status|submitted|pending|present"
    r")\b"
)


def _is_email_body(it: Item) -> bool:
    return (it.name or "").lower().startswith("email body")


# A staff/employee number embedded in a filename — either an explicit label
# ("Staff No: 46533", "Emp ID 46533") or the bare "(46533)" convention a
# forwarded-per-employee attachment is commonly renamed to. Used ONLY to STOP
# a same-template dedup between two clearly different, named employees (a
# bulk send of one identical timesheet layout per person scores as "near
# duplicate" on text alone) — never to positively match, so a filename with
# no such number falls through to today's text-only behaviour unchanged.
_FILENAME_ID_RE = re.compile(
    r"(?:staff\s*no\.?|staff\s*number|emp(?:loyee)?\s*(?:id|no)\.?)\s*[:#]?\s*(\d{3,})"
    r"|\((\d{3,})\)",
    re.I,
)


def _filename_identity(name: str) -> str | None:
    m = _FILENAME_ID_RE.search(name or "")
    if not m:
        return None
    return m.group(1) or m.group(2)


def _sheet_text_fingerprint(text: str) -> str:
    """Normalize OCR/PDF text for near-dup: collapse whitespace, drop
    approval/sign/manager/workflow-status noise that differs between unsigned
    vs signed regenerations of the same timesheet."""
    t = (text or "").lower()
    t = _APPROVAL_LINE_RE.sub(" ", t)
    t = _APPROVAL_NOISE_RE.sub(" ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def _texts_near_match(a: str, b: str, *, min_chars: int = 40, ratio: float = 0.92) -> bool:
    """True when two sheets share the same core content after approval-noise
    strip (exact fingerprint or very high SequenceMatcher ratio)."""
    from difflib import SequenceMatcher

    fa, fb = _sheet_text_fingerprint(a), _sheet_text_fingerprint(b)
    if not fa or not fb:
        return False
    if fa == fb:
        return True
    if min(len(fa), len(fb)) < min_chars:
        return False
    return SequenceMatcher(None, fa, fb).ratio() >= ratio


def _approval_signal_score(it: Item) -> tuple:
    """Prefer the copy that carries manager approval / sign. Higher = keep."""
    t = (it.text or "").lower()
    score = 0
    for kw, w in (
        ("approved by", 8),
        ("date of approval", 4),
        ("approved", 3),
        ("signature", 3),
        ("signed", 3),
        ("approver", 3),
        ("line manager", 2),
        ("manager", 1),
    ):
        if kw in t:
            score += w
    # Later reply often has the regenerated signed PDF.
    score += it.msg_index * 2
    # Signatures add a few KB; cap so size alone cannot dominate.
    score += min(max(it.size, 0) // 2000, 40)
    return (score, len(it.text or ""), it.msg_index, it.key)


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
    image_path = None
    if image:
        from app.services.pipeline import raw_store
        fname = f"dropped-{len(cap.dropped_items)}-{entry.get('name') or 'item'}"
        image_path = raw_store.save_raw(f"debug/{cap.run_id}", fname, image)
    debug_capture.record_dropped(**entry, image_path=image_path)


def _dedupe_sheet_items(th: Thread) -> None:
    """Collapse byte-identical and near-identical sheet attachments.

    Exact same bytes → keep one. Same core OCR/PDF text with only small
    manager-sign / Approved By diffs → keep the stronger approval copy,
    drop the other into ``th.dropped`` with ``filter=dup`` for the Extract UI.
    Email bodies are never merged.
    """
    keepers: list[Item] = []
    for it in th.items:
        if _is_email_body(it):
            keepers.append(it)
            continue
        matched_idx: int | None = None
        exact = False
        for i, kept in enumerate(keepers):
            if _is_email_body(kept):
                continue
            if it.digest and kept.digest and it.digest == kept.digest:
                matched_idx = i
                exact = True
                break
            id_a, id_b = _filename_identity(it.name), _filename_identity(kept.name)
            if id_a and id_b and id_a != id_b:
                # Filenames name two different employees (e.g. a bulk send of
                # one identical timesheet template per person) — never a
                # duplicate no matter how similar the sheet text scores.
                continue
            if _texts_near_match(it.text, kept.text):
                matched_idx = i
                exact = False
                break
        if matched_idx is None:
            keepers.append(it)
            continue
        kept = keepers[matched_idx]
        if _approval_signal_score(it) > _approval_signal_score(kept):
            winner, loser = it, kept
            keepers[matched_idx] = it
        else:
            winner, loser = kept, it
        reason = (
            f"duplicate of [{winner.key}] {winner.name} (identical bytes)"
            if exact
            else (
                f"duplicate of [{winner.key}] {winner.name} "
                "(same sheet text; kept version with stronger approval/sign)"
            )
        )
        _record_dropped(th, {
            "name": loser.name,
            "mime": loser.mime,
            "size": loser.size,
            "msg_index": loser.msg_index,
            "key": loser.key,
            "filter": "dup",
            "reason": reason,
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
        ext = os.path.splitext(name)[1].lower()
        is_image = ext in IMAGE_EXTS or (ctype or "").startswith("image/")

        # Size filter FIRST — logos, signature icons and social buttons never
        # reach any further processing (no OCR call spent on them either).
        if is_image and settings.min_image_bytes and len(payload) < settings.min_image_bytes:
            size_jpeg = _image_jpeg(payload)
            _record_dropped(th, {
                "name": name, "mime": ctype, "size": len(payload), "msg_index": msg_index,
                "filter": "size",
                "reason": f"image under {settings.min_image_bytes // 1024}KB",
                "thumb": _thumb_uri(size_jpeg),
            }, size_jpeg)
            return

        # An email attached as a FILE (not MIME-nested) — unwrap recursively,
        # same as a forward the mail structure already nests for free.
        if ext in (".eml", ".msg"):
            if depth + 1 > MAX_EML_DEPTH:
                return
            sub_bytes = msg_to_eml_bytes(payload) if ext == ".msg" else payload
            if not sub_bytes:
                return
            try:
                sub = _email.message_from_bytes(sub_bytes, policy=_email.policy.default)
            except Exception:
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
                return
            from app.services.extraction.ocr import ocr_status, ocr_text
            # Only apply the noise filter when OCR can actually run — the
            # reader returns "" (not an error) on a missing engine binary,
            # which would otherwise read as "no text, drop it" and silently
            # discard every real picture attachment. No reader available =
            # every picture attachment is kept, exactly like the notebook.
            if ocr_status() == "ready":
                ocr = ocr_text([jpeg], payload, "image")
                if len(ocr.strip()) < settings.ocr_min_chars:
                    _record_dropped(th, {
                        "name": name, "mime": ctype, "size": len(payload), "msg_index": msg_index,
                        "filter": "ocr",
                        "ocr_chars": len(ocr.strip()),
                        "reason": f"OCR found only {len(ocr.strip())} char(s) "
                                  f"(<{settings.ocr_min_chars}) - likely a logo/icon",
                        "thumb": _thumb_uri(jpeg),
                    }, jpeg)
                    return
            it.images = [jpeg]
            it.send_mode = "image (only option for a picture)"
        elif ext in (".xlsx", ".xlsm", ".xls") or ftype == "xlsx":
            _fill_office_item(it, payload, "xlsx", ext or ".xlsx")
        elif ext == ".csv":
            it.text = scrub_text(payload.decode("utf-8", "replace")[:20000])
            it.send_mode = "native"
        elif ext == ".docx" or ftype == "docx":
            _fill_office_item(it, payload, "docx", ext or ".docx")
        elif ext == ".txt":
            it.text = scrub_text(payload.decode("utf-8", "replace")[:20000])
            it.send_mode = "native"
        else:
            it.text = f"[unsupported attachment type {ctype or ftype or 'unknown'}]"
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
                if sub is not None and depth + 1 <= MAX_EML_DEPTH:
                    # Outlook lists these as named .eml attachments — record
                    # the outer filename for the Extracted badge.
                    fname = part.get_filename()
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
        m.body = scrub_text(body)[:12000]

    for _label, eml_bytes in messages:
        try:
            root = _email.message_from_bytes(eml_bytes, policy=_email.policy.default)
        except Exception:
            continue
        walk(root, 0)

    # Every message's body becomes its own item too, so pass 1 can classify a
    # pasted grid or a pasted approval note the same as any attachment — as
    # plain text, never as an image.
    for m in th.messages:
        if m.body.strip():
            th.items.append(Item(key=new_key(), name=f"email body (message {m.index + 1})",
                                 mime="text/plain", msg_index=m.index, text=m.body))
    # Collapse re-attached / regenerated copies of the same timesheet before
    # pass 1 — identical bytes or same core OCR with only manager-sign diffs.
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
            where = "sheet" if sig else "conversation"
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
        "period": t.get("period_hint"),
        "signature": bool(t.get("manager_signature")),
        "notes": t.get("notes") or "",
        "thumb": _item_thumb(it) if it is not None else None,
        "key": it.key if it is not None else None,
    }


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
            "_source_key": source,
            "kind": t.get("kind", "timesheet"),
            "employee_name": (str(s.get("employee_name")).strip() or None)
            if s.get("employee_name") else t.get("employee_name"),
            "employee_id": (str(s.get("employee_id")).strip() or None)
            if s.get("employee_id") else t.get("employee_id"),
            "month": month,
            "year": year,
            "days_covered": max(0, int(s.get("days_covered") or 0)) if str(s.get("days_covered") or "").strip() else 0,
            "period_type": str(s.get("period_type") or "unknown").lower()
            if str(s.get("period_type") or "").lower() in
               ("full_month", "half_month", "week", "partial", "unknown") else "unknown",
            "missing_days": [int(d) for d in (s.get("missing_days") or [])
                            if str(d).strip().lstrip("-").isdigit()
                            and 1 <= int(d) <= (
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
            entry = cached_sheets.get(it.digest) if it.digest else None
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
    batches = triage_prompt.chunk_items_by_count(th.items, settings.pass1_batch_size)
    emit("pass1", "spin",
         f"Pass 1 of 2 — {model} is reading the whole conversation "
         f"({len(batches)} call(s))…",
         pass_no=1, model=model, message_count=len(messages))

    all_items: list = []
    thread_summary = ""
    for bi, batch in enumerate(batches, 1):
        note = (f"This is batch {bi} of {len(batches)}. You are seeing SOME of the thread's "
                f"items below; classify ONLY those. Other batches cover the rest, but all "
                f"message bodies are included so you have full approval context."
                if len(batches) > 1 else "")
        # Sent -> received bracket around the actual network call, so the live
        # UI can show "waiting for a response" instead of a step that looks
        # frozen for however long one vision call takes — and, with more than
        # one batch, exactly which one is in flight right now.
        emit("pass1", "spin",
             f"Pass 1 — sending batch {bi} of {len(batches)} ({len(batch)} item(s)) to {model}…",
             pass_no=1, model=model, batch_index=bi, batch_total=len(batches), batch_phase="sent")
        data = await triage_prompt.run_pass1_batch(th, batch, note, model, api_key, label=f"pass1-batch{bi}")
        count_llm()
        emit("pass1", "spin",
             f"Pass 1 — batch {bi} of {len(batches)} received.",
             pass_no=1, model=model, batch_index=bi, batch_total=len(batches), batch_phase="received")
        all_items += (data.get("items") or [])
        if not thread_summary:
            thread_summary = (data.get("thread_summary") or "").strip()

    def _noise_label(source: str) -> str:
        it = resolve_source(source, th.items)
        return it.name if it is not None else source

    noise = [_noise_label(str(m.get("source"))) for m in all_items
             if isinstance(m, dict) and (m.get("kind") or "").lower() == "noise"]
    triage = _normalise_pass1_items(
        [m for m in all_items if isinstance(m, dict) and (m.get("kind") or "").lower() != "noise"])

    # Approval/summary reason over fresh AND reused sheets together, so a
    # signature found on a message outside this run's window (or already
    # confirmed on a cached item inside it) is never forgotten just because
    # this run didn't re-read it.
    combined_triage = triage + reused_triage
    approval = _derive_approval(combined_triage)
    summary_obj = _build_summary(combined_triage, approval, thread_summary, len(batches))
    data_items = [t for t in triage if t["kind"] in ("timesheet", "leave_certificate")]
    full_summary = _enrich_thread_summary(
        summary_obj, triage=combined_triage, approval=approval,
        message_count=len(messages), model=model)

    emit("pass1", "ok",
         f"Pass 1 done — {len(data_items)} timesheet/certificate(s) identified"
         + (f", {len(noise)} noise item(s) ignored" if noise else "") + ".",
         pass_no=1,
         items=[_ui_pass_item(t, th.items) for t in triage],
         confirmed=[_ui_pass_item(t, th.items) for t in data_items],
         employees=[t.get("employee_name") for t in data_items if t.get("employee_name")],
         noise=noise, approval=approval,
         summary=full_summary,
         thread_summary=full_summary)

    meta = {
        "method": "thread-two-pass", "model": model, "calls": len(batches),
        "sheet_count": 0, "errors": [], "skipped": th.dropped, "conflicts": [],
        "noise": noise, "summary": full_summary.get("headline", ""),
        "summary_obj": full_summary, "triage": triage,
        "thread_summary": full_summary,
        "_opened_containers": list(th.opened_containers),
    }

    if not data_items:
        if reused_sheets:
            meta["sheet_count"] = len(reused_sheets)
            meta["reused_sheets"] = len(reused_sheets)
            return reused_sheets, approval, [], meta
        return [], approval, [], meta

    # ---- PASS 2: read only the confirmed items -----------------------------
    pairs = thread_prompt.confirmed_sheets(data_items, th.items)
    if not pairs:
        if reused_sheets:
            meta["sheet_count"] = len(reused_sheets)
            meta["reused_sheets"] = len(reused_sheets)
            return reused_sheets, approval, [], meta
        meta["errors"] = ["pass 1 confirmed sheets that could not be resolved to any item"]
        emit("extract", "warn", meta["errors"][0])
        return [], approval, [], meta

    batches2: list[list] = []
    cur: list = []
    n = 0
    for pair in pairs:
        k = max(len(pair[0].images), 1)
        if cur and n + k > settings.max_images_per_call:
            batches2.append(cur)
            cur, n = [], 0
        cur.append(pair)
        n += k
    if cur:
        batches2.append(cur)

    needs_body_context = any(it.name.startswith("email body") for it, _ in pairs)
    body_text = "\n".join(m.body for m in th.messages)[:8000] if needs_body_context else ""

    emit("pass2", "spin",
         f"Pass 2 of 2 — extracting {len(pairs)} confirmed sheet(s) ({len(batches2)} call(s))…",
         pass_no=2, model=model, sheets=[it.name for it, _ in pairs])

    raw_sheets: list = []
    pass2_call_count = 0
    for bi, batch in enumerate(batches2, 1):
        # Same sent -> received bracket as pass 1: the UI shows "waiting for a
        # response" for the actual duration of the network call, not a step
        # that just looks stuck, and (with more than one batch) which one.
        emit("pass2", "spin",
             f"Pass 2 — sending batch {bi} of {len(batches2)} ({len(batch)} sheet(s)) to {model}…",
             pass_no=2, model=model, batch_index=bi, batch_total=len(batches2), batch_phase="sent")
        data = await thread_prompt.run_pass2_batch(
            batch, body_text, model, api_key, calendars=calendars, label=f"pass2-batch{bi}")
        count_llm()
        pass2_call_count += 1
        emit("pass2", "spin",
             f"Pass 2 — batch {bi} of {len(batches2)} received.",
             pass_no=2, model=model, batch_index=bi, batch_total=len(batches2), batch_phase="received")
        sheets_out = data.get("sheets") or []
        if not sheets_out and batch:
            # Every item in this batch was already CONFIRMED by pass 1 (which
            # read these same images successfully moments earlier), so a
            # flatly empty reply here is far more likely a bad/degenerate
            # model generation than "these are genuinely blank documents" —
            # retry once before accepting nothing for real, confirmed items.
            emit("pass2", "spin",
                 f"Pass 2 batch {bi} of {len(batches2)} returned nothing for "
                 f"{len(batch)} confirmed item(s) — retrying once…",
                 pass_no=2, model=model, batch_index=bi, batch_total=len(batches2), batch_phase="retry")
            data = await thread_prompt.run_pass2_batch(
                batch, body_text, model, api_key, calendars=calendars, label=f"pass2-batch{bi}-retry")
            count_llm()
            pass2_call_count += 1
            emit("pass2", "spin",
                 f"Pass 2 — batch {bi} of {len(batches2)} received (retry).",
                 pass_no=2, model=model, batch_index=bi, batch_total=len(batches2), batch_phase="received")
            sheets_out = data.get("sheets") or []
        raw_sheets += sheets_out

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
    emit("pass2", "ok", f"Pass 2 done — {len(sheets)} sheet(s), {total_days} day(s) accounted for.",
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

    meta["calls"] = len(batches) + pass2_call_count
    meta["sheet_count"] = len(sheets)
    return sheets, approval, [], meta
