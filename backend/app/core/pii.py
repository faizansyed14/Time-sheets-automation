"""
PII redaction for content sent to external AI providers.

Threat model (product decision):
- Emp name / ID / leave dates on timesheet PDFs & DOCX → ALLOWED (needed to extract).
- Email addresses, phones, credentials, To/From/Cc headers, signature footers
  (after Thanks / Regards) → MUST NOT reach the model.

``scrub_text``         — general text (emails, phones, secrets).
``scrub_email_for_llm`` — email subject + body: headers softened, signature cut,
                          then scrub_text. Used for body→JPEG and prompt text.
``PII_REDACTION=false`` disables all of the above (dev A/B only).
"""
from __future__ import annotations

import hashlib
import re

from app.core.config import settings

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+"
)

# Zero-width / bidi marks Outlook inserts around phones in HTML signatures.
_INVISIBLE_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u200e\u200f]")

_INTL_PHONE_RE = re.compile(r"(?<![\w.+\-])\+\d{1,3}(?:[\s().\-]*\d){7,12}(?!\d)")

# Word labels (Tel/Mobile/…) plus short Outlook T: / M: (require punctuation
# so "AM"/"PM" clock suffixes are never eaten).
_LABELLED_PHONE_RE = re.compile(
    r"(?i)(?:"
    r"\b(phone|telephone|tel|mobile|mob|cell|fax|whatsapp)\b"
    r"(\s*(?:no|number|#)?\s*[:.\-]?\s*)"
    r"|"
    r"(?<![A-Za-z])([TM])(\s*[:.\-]\s*)"
    r")"
    r"(\+?\(?\d[\d\s().\-]{4,18}\d)"
)

# Cut corporate contact cards after printed sheet signature lines (no Thanks/).
_SHEET_SIG_LABEL_RE = re.compile(
    r"(?im)^.*\b(?:EMPLOYEE|MANAGER)\s+SIGNATURE\b.*$"
)

# Outlook / Gmail quoted reply history — everything after is prior messages.
_QUOTE_THREAD_RE = re.compile(
    r"(?im)^(?:"
    r"From:\s+.+\r?\n(?:.*\r?\n){0,5}(?:Sent|Date):\s+|"
    r"-{2,}\s*Original Message\s*-{2,}|"
    r"_{5,}\s*$|"
    r"On .{10,120} wrote:\s*$"
    r")"
)

_HTML_QUOTE_THREAD_RE = re.compile(
    r"(?is)(?:"
    r"<div[^>]+(?:id|name|class)=[\"'][^\"']*(?:divRplyFwdMsg|OutlookMessageHeader|gmail_quote)|"
    r"<hr\b[^>]*>\s*(?:<(?:br|div|p|span|b|strong|font)[^>]*>\s*){0,12}"
    r"From:\s*|"
    r"<blockquote\b"
    r")"
)

_DATE_SHAPED_RE = re.compile(r"\d{1,4}([./\-\s])\d{1,2}\1\d{1,4}$")

# Credentials / secrets often pasted under a signature after "Thanks".
_SECRET_RE = re.compile(
    r"(?i)\b("
    r"password|passwd|pwd|passcode|pass\s*phrase|pin|"
    r"secret(?:\s*key)?|api[_\s-]?key|access[_\s-]?key|"
    r"vpn(?:\s*password)?|wifi(?:\s*password)?|login\s*password|"
    r"temporary\s*password|one[-\s]?time(?:\s*password|code)?"
    r")\b"
    r"(\s*(?:is|=|:)?\s*)"
    r"(\S.{0,80}?)(?=\s*(?:[\r\n]|$|[.;,](?:\s|$)))"
)

# Quoted reply / forward envelope lines — addresses scrubbed; line kept for context.
_HEADER_LINE_RE = re.compile(
    r"(?im)^(from|to|cc|bcc|reply-to|sender|delivered-to|return-path)\s*:"
    r".+$"
)

# Start of a typical corporate signature / closing.
_SIGNATURE_START_RE = re.compile(
    r"(?im)^(?:"
    r"--\s*$|"
    r"_{3,}\s*$|"
    r"-{3,}\s*$|"
    r"(?:with\s+)?(?:kind\s+)?regards?\b.*$|"
    r"best(?:\s+regards)?\b.*$|"
    r"thanks?(?:\s+and\s+regards)?\b.*$|"
    r"thank\s+you\b.*$|"
    r"sincerely\b.*$|"
    r"cheers\b.*$|"
    r"yours\s+(?:truly|faithfully|sincerely)\b.*$|"
    r"sent\s+from\s+my\s+(?:iphone|ipad|android|mobile)\b.*$"
    r")"
)

# Keep short approval-only bodies intact (cutting would remove the only content).
_APPROVAL_HINT_RE = re.compile(
    r"(?i)\b(approved|approval\s+granted|ok(?:ay)?\s+to\s+process|looks\s+good|lgtm)\b"
)

PHONE_TOKEN = "[phone-redacted]"
SECRET_TOKEN = "[secret-redacted]"
HEADER_VALUE_TOKEN = "[header-redacted]"
SIGNATURE_NOTE = "\n\n[signature-redacted]"
THREAD_QUOTE_NOTE = "\n\n[quoted-thread-redacted]"


def pseudonymize_email(address: str) -> str:
    digest = hashlib.sha256(address.strip().lower().encode()).hexdigest()[:6]
    return f"person-{digest}@redacted.invalid"


def _mask_labelled_phone(m: re.Match) -> str:
    # Groups: (word_label)(word_sep) | (tm_label)(tm_sep) then (number)
    label = m.group(1) or m.group(3) or ""
    sep = m.group(2) if m.group(1) is not None else (m.group(4) or "")
    number = m.group(5) or ""
    if _DATE_SHAPED_RE.fullmatch(number.strip()):
        return m.group(0)
    return f"{label}{sep}{PHONE_TOKEN}"


def _mask_secret(m: re.Match) -> str:
    return f"{m.group(1)}{m.group(2)}{SECRET_TOKEN}"


def _mask_header_line(m: re.Match) -> str:
    """Keep the header name; drop addresses / phones from the value."""
    label = m.group(1)
    return f"{label}: {HEADER_VALUE_TOKEN}"


def strip_email_headers(text: str) -> str:
    """Replace From/To/Cc/Bcc/Reply-To line values (quoted reply envelopes)."""
    if not text:
        return ""
    return _HEADER_LINE_RE.sub(_mask_header_line, text)


def strip_quoted_reply_thread(text: str) -> str:
    """Keep only the latest message — drop Outlook/Gmail quoted history.

    Long reply chains dump every prior From/To/Cc + signature into the body
    JPEG; the timesheet itself is almost always an attachment or the top note.
    """
    if not text or not text.strip():
        return text or ""
    m = _QUOTE_THREAD_RE.search(text)
    if not m or m.start() < 20:
        return text
    return text[: m.start()].rstrip() + THREAD_QUOTE_NOTE


def strip_html_quoted_reply_thread(html: str) -> str:
    """Same as strip_quoted_reply_thread for HTML bodies (hr / Outlook wrappers)."""
    if not html or not html.strip():
        return html or ""
    m = _HTML_QUOTE_THREAD_RE.search(html)
    if not m or m.start() < 40:
        return html
    return html[: m.start()].rstrip() + "<!-- quoted-thread-redacted -->"


def strip_signature_block(text: str) -> str:
    """Cut corporate footers after Thanks/Regards so emails/phones/passwords
    in the signature never become model pixels or prompt text.

    Short approval-only notes that ARE the whole body are left intact so
    'Approved. Thanks' still reaches the model.

    Also cuts contact cards after EMPLOYEE/MANAGER SIGNATURE lines (common on
    HTML attendance sheets that never say Thanks/Regards).
    """
    if not text or not text.strip():
        return text or ""
    out = text
    m = _SIGNATURE_START_RE.search(out)
    if m:
        before = out[: m.start()].rstrip()
        keep_short_approval = (
            len(before) < 40 and _APPROVAL_HINT_RE.search(out)
        ) or len(before) < 12
        if not keep_short_approval:
            out = before + SIGNATURE_NOTE

    sheet_matches = list(_SHEET_SIG_LABEL_RE.finditer(out))
    if sheet_matches:
        end = sheet_matches[-1].end()
        after = out[end:].strip()
        # Only cut when a real footer follows (phones, brand, second card).
        if len(after) >= 40 or re.search(
            r"(?i)(?:\b(?:tel|mobile)\b|[TM]\s*:|\+\d{1,3})", after
        ):
            head = out[:end].rstrip()
            if SIGNATURE_NOTE.strip() not in head:
                return head + SIGNATURE_NOTE
            return head
    return out


def scrub_text(text: str | None) -> str:
    """Mask emails, unambiguous phones, and credential values. Names, employee
    IDs, dates and hours stay byte-identical."""
    if not text:
        return ""
    if not settings.pii_redaction:
        return text
    out = _INVISIBLE_RE.sub("", text)
    out = strip_email_headers(out)
    out = _EMAIL_RE.sub(lambda m: pseudonymize_email(m.group(0)), out)
    out = _SECRET_RE.sub(_mask_secret, out)
    out = _INTL_PHONE_RE.sub(PHONE_TOKEN, out)
    out = _LABELLED_PHONE_RE.sub(_mask_labelled_phone, out)
    return out


def scrub_email_for_llm(
    subject: str | None,
    body: str | None,
    *,
    cut_signature: bool = True,
) -> tuple[str, str]:
    """Subject + body as they will be shown to the vision model.

    Order: drop quoted reply thread → optional signature cut → scrub_text
    (headers / emails / phones / secrets). Timesheet attachment files are NOT
    passed through this — only email subject/body content.
    """
    subj = subject or ""
    raw = body or ""
    if not settings.pii_redaction:
        return subj, raw
    body_out = strip_quoted_reply_thread(raw)
    if cut_signature:
        body_out = strip_signature_block(body_out)
    return scrub_text(subj), scrub_text(body_out)


def assert_no_plaintext_pii(sample: str, *, canaries: list[str]) -> list[str]:
    """Return canary strings still present (empty = clean). Used by tests."""
    hits = []
    low = sample or ""
    for c in canaries:
        if c and c in low:
            hits.append(c)
    return hits
