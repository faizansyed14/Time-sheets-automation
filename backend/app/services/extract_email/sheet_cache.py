"""Record of WHICH attachments Extract Email has already read.

Every run re-reads every attachment — reusing a stored result was tried and
removed, because it made a bad read permanent: a sheet marked "LEAVE
(MEDICAL)" was booked as ANNUAL leave, and re-extracting served the same wrong
answer back because the file had not changed. Correcting the prompt has to be
enough to correct the data.

What remains is a RECORD, not a cache: keyed by a hash of the file's bytes, it
says "this attachment has been looked at", which drives the Extracted/New badge
in the inbox. It is never read back as an answer.

Stored on the EmailMessage row so it shares that row's lifetime and survives an
inbox resync (_sync_message only overwrites the columns it lists).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.email_message import EmailMessage

# Recorded alongside each entry so it is always clear which prompt produced a
# stored reading. Nothing is served back from it — it is provenance, not a key.
PROMPT_VERSION = "thread-v1"


def content_key(payload: bytes) -> str:
    """Stable id for a file's bytes."""
    return hashlib.sha256(payload or b"").hexdigest()


async def remember(
    db: AsyncSession, message_id: str, model: str, sheets_by_digest: dict[str, dict],
) -> None:
    """Record freshly extracted sheets against the message they arrived on."""
    if not (message_id and sheets_by_digest):
        return
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == message_id))).scalar_one_or_none()
    if row is None:
        return
    store = dict(row.extracted_sheets or {})
    now = datetime.now(timezone.utc).isoformat()
    for digest, sheet in sheets_by_digest.items():
        store[digest] = {
            "filename": sheet.get("name"),
            "at": now,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "sheet": sheet,
        }
    row.extracted_sheets = store
    flag_modified(row, "extracted_sheets")   # JSON column, mutated in place
    await db.commit()


def extracted_filenames(row: EmailMessage) -> list[str]:
    """Names of this message's attachments that have already been read —
    what the inbox marks "Extracted" rather than "New"."""
    names: list[str] = []
    for entry in (row.extracted_sheets or {}).values():
        fn = (entry or {}).get("filename")
        if fn and fn not in names:
            names.append(fn)
    return names
