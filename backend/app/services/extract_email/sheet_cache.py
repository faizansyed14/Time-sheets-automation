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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.email_message import EmailMessage
from app.models.pipeline_file import PipelineFile
from app.services.extract_email.constants import TAG_PREFIX

# Recorded alongside each entry so it is always clear which prompt produced a
# stored reading. Nothing is served back from it — it is provenance, not a key.
PROMPT_VERSION = "thread-v1"


def content_key(payload: bytes) -> str:
    """Stable id for a file's bytes."""
    return hashlib.sha256(payload or b"").hexdigest()


async def remember(
    db: AsyncSession, message_id: str, model: str, sheets_by_digest: dict[str, dict],
    *, containers: list[dict] | None = None,
) -> None:
    """Record freshly extracted sheets against the message they arrived on.

    `containers` are outer .eml/.msg filenames that were unwrapped to reach
    inner sheets — badge-only entries so the Inbox can mark those attachments
    Extracted (their names never appear as analysable items)."""
    if not message_id or (not sheets_by_digest and not containers):
        return
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == message_id))).scalar_one_or_none()
    if row is None:
        return
    store = dict(row.extracted_sheets or {})
    now = datetime.now(timezone.utc).isoformat()
    for digest, sheet in (sheets_by_digest or {}).items():
        store[digest] = {
            "filename": sheet.get("name"),
            "at": now,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "sheet": sheet,
        }
    for c in containers or []:
        digest = (c or {}).get("digest")
        name = (c or {}).get("name")
        if not digest or not name:
            continue
        existing = store.get(digest)
        # A real sheet entry for the same bytes wins over a badge-only marker.
        if existing and existing.get("sheet") and not existing.get("badge_only"):
            continue
        store[digest] = {
            "filename": name,
            "at": now,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "badge_only": True,
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


async def thread_cached_sheets(
    db: AsyncSession, conversation_id: str | None, message_ids: list[str] | None = None,
) -> dict[str, dict]:
    """Every previously-extracted attachment for a whole conversation, keyed by
    content hash — {digest: {filename, at, model, prompt_version, sheet}}.

    `extracted_sheets` is written to whichever single message happened to be a
    past run's anchor, so it is scattered across rows of the same conversation
    rather than aggregated. This unions all of them (newest `at` wins on a
    collision) so incremental re-extraction can reuse anything ever read for
    this thread, not just what happens to be on one row.

    Returns {} immediately for a falsy `conversation_id`/`message_ids` (e.g.
    Upload, which has no conversation) — no query, no caching overhead."""
    if conversation_id:
        stmt = select(EmailMessage.extracted_sheets).where(
            EmailMessage.conversation_id == conversation_id)
    elif message_ids:
        stmt = select(EmailMessage.extracted_sheets).where(
            EmailMessage.provider_message_id.in_(message_ids))
    else:
        return {}

    merged: dict[str, dict] = {}
    for (sheets,) in (await db.execute(stmt)).all():
        for digest, entry in (sheets or {}).items():
            if not isinstance(entry, dict):
                continue
            existing = merged.get(digest)
            if existing is None or str(entry.get("at", "")) > str(existing.get("at", "")):
                merged[digest] = entry
    return merged


async def last_extraction_at(db: AsyncSession, thread_key: str | None) -> datetime | None:
    """When Extract Email last ran on this thread (any run started from any
    message in it counts), or None if it never has. Drives incremental
    windowing — messages received after this point are "new"; everything
    before is presumed already read (and reusable from the cache above).

    Returns None immediately for a falsy `thread_key` (Upload has none)."""
    if not thread_key:
        return None
    return (await db.execute(
        select(func.max(PipelineFile.updated_at)).where(
            PipelineFile.source_kind == "email",
            PipelineFile.thread_key == thread_key,
            PipelineFile.attachment_id.like(f"{TAG_PREFIX}%"),
        )
    )).scalar_one_or_none()


async def last_extraction_at_bulk(
    db: AsyncSession, thread_keys: list[str],
) -> dict[str, datetime]:
    """Same lookup as last_extraction_at(), for every thread at once — one
    query instead of one-per-thread. Built for Auto Extract's bulk loop,
    which needs this for every thread in the mailbox up front (to know which
    are skip-eligible before it starts) rather than one at a time as it goes.

    A thread absent from the mailbox never appears in the returned dict —
    callers use .get(thread_key) and treat a miss as "never extracted",
    identical to last_extraction_at() returning None."""
    if not thread_keys:
        return {}
    rows = (await db.execute(
        select(PipelineFile.thread_key, func.max(PipelineFile.updated_at))
        .where(
            PipelineFile.source_kind == "email",
            PipelineFile.thread_key.in_(thread_keys),
            PipelineFile.attachment_id.like(f"{TAG_PREFIX}%"),
        )
        .group_by(PipelineFile.thread_key)
    )).all()
    return {thread_key: at for thread_key, at in rows}
