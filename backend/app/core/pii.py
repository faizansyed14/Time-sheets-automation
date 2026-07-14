"""
PII redaction for content sent to external AI providers.

Every prompt — and every email body rendered to an image — passes through
``scrub_text`` before leaving the server. Redaction is deliberately
conservative so extraction accuracy is untouched:

- Email addresses  -> a stable pseudonym (person-3f2a1b@redacted.invalid).
  Stable means the same address always maps to the same token, so the model
  can still tell two correspondents apart across a forwarded thread.
  Addresses are never an identity source for extraction: the prompts forbid
  it, and sender matching runs locally against the DB after the model call.
- Phone numbers    -> only unambiguous ones are masked: international
  (+XX...) format, or numbers behind a phone/mobile/tel/fax label. Bare
  digit runs are LEFT ALONE — they could be employee IDs, dates or hours,
  which the extraction must read exactly. A date-shaped value is never
  masked even when it sits behind a label.
- Names, employee IDs, dates and hours are never touched — they are the
  data being extracted and are matched locally against the employee DB.

Set PII_REDACTION=false to disable (e.g. to A/B extraction accuracy).
"""
from __future__ import annotations

import hashlib
import re

from app.core.config import settings

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+"
)

# International format: "+<country code>" then 7-12 more digits with optional
# separators. Dates and clock times never start with "+".
_INTL_PHONE_RE = re.compile(r"(?<![\w.+\-])\+\d{1,3}(?:[\s().\-]*\d){7,12}(?!\d)")

# A local-format number is masked only behind an explicit phone label.
_LABELLED_PHONE_RE = re.compile(
    r"(?i)\b(phone|telephone|tel|mobile|mob|cell|fax|whatsapp)\b"
    r"(\s*(?:no|number|#)?\s*[:.\-]?\s*)"
    r"(\+?\(?\d[\d\s().\-]{4,18}\d)"
)

# "05-06-2026" / "5.6.26" / "05 06 2026" — same separator both times.
_DATE_SHAPED_RE = re.compile(r"\d{1,4}([./\-\s])\d{1,2}\1\d{1,4}$")

PHONE_TOKEN = "[phone-redacted]"


def pseudonymize_email(address: str) -> str:
    digest = hashlib.sha256(address.strip().lower().encode()).hexdigest()[:6]
    return f"person-{digest}@redacted.invalid"


def _mask_labelled_phone(m: re.Match) -> str:
    if _DATE_SHAPED_RE.fullmatch(m.group(3).strip()):
        return m.group(0)
    return f"{m.group(1)}{m.group(2)}{PHONE_TOKEN}"


def scrub_text(text: str | None) -> str:
    """Mask email addresses and unambiguous phone numbers; everything else is
    returned byte-identical. Safe on None/empty input."""
    if not text:
        return ""
    if not settings.pii_redaction:
        return text
    out = _EMAIL_RE.sub(lambda m: pseudonymize_email(m.group(0)), text)
    out = _INTL_PHONE_RE.sub(PHONE_TOKEN, out)
    out = _LABELLED_PHONE_RE.sub(_mask_labelled_phone, out)
    return out
