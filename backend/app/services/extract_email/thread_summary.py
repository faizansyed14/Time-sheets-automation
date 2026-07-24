"""Storage for the conversation summary Extract Email produces.

The summary is a PRODUCT OF PASS 1 of the extraction (see triage_prompt.py) —
the same call that decides which items are timesheets also says, in plain
English, what the thread is about and what is outstanding. There is no separate
summarisation call: a second model read of text the first pass had already
seen cost money to restate what was known, and the two could disagree.

This module is only the read/write side: stored on the EmailMessage row it was
generated from, read back by conversation so it shows no matter which message
is opened, and preserved across inbox resync (_sync_message only overwrites the
columns it lists).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.email_message import EmailMessage


async def save_summary(db: AsyncSession, message_id: str, summary: dict) -> None:
    """Store the summary against the message the run started from."""
    row = (await db.execute(select(EmailMessage).where(
        EmailMessage.provider_message_id == message_id))).scalar_one_or_none()
    if row is None:
        return
    row.thread_summary = summary
    flag_modified(row, "thread_summary")
    await db.commit()


async def load_summary(
    db: AsyncSession, conversation_id: str | None, message_ids: list[str],
) -> dict | None:
    """The newest stored summary for this conversation.

    Read by conversation so it shows no matter which message is opened, while
    only one row actually holds it."""
    stmt = select(EmailMessage).where(
        (EmailMessage.conversation_id == conversation_id) if conversation_id
        else EmailMessage.provider_message_id.in_(message_ids))
    best: dict | None = None
    for row in (await db.execute(stmt)).scalars():
        s = row.thread_summary
        if isinstance(s, dict) and (best is None or str(s.get("at", "")) > str(best.get("at", ""))):
            best = s
    return best
