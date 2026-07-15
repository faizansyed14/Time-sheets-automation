#!/usr/bin/env python3
"""Reset inbox workflow state — archived / ingested / extracted / no-sheets → new.

Clears per-email decision markers and deletes email-sourced pipeline items
(including Extract Email staging) so the inbox looks untouched. Does NOT delete
timesheet records, vault files, or employees.

  bash scripts/db/reset-inbox.sh
  bash scripts/db/reset-inbox.sh --dry-run
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, func, or_, select, update

from app.core.database import SessionLocal
from app.models.email_message import EmailMessage, EmailStatus
from app.models.pipeline_file import PipelineFile
from app.services.pipeline.ingestion import purge_raw_copy

_RESET_STATUSES = (EmailStatus.INGESTED, EmailStatus.ARCHIVED)


async def _dirty_email_ids(db) -> list[str]:
    """provider_message_ids that are not a clean untouched inbox row."""
    has_pipeline = (
        select(PipelineFile.id)
        .where(
            PipelineFile.source_kind == "email",
            PipelineFile.source_id == EmailMessage.provider_message_id,
        )
        .exists()
    )
    rows = (
        await db.execute(
            select(EmailMessage.provider_message_id).where(
                or_(
                    EmailMessage.status.in_(_RESET_STATUSES),
                    EmailMessage.decided_at.isnot(None),
                    EmailMessage.no_sheets_found_at.isnot(None),
                    has_pipeline,
                )
            )
        )
    ).scalars().all()
    return list(rows)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Reset inbox emails to status=new.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing.",
    )
    args = parser.parse_args()

    async with SessionLocal() as db:
        ids = await _dirty_email_ids(db)
        if not ids:
            print("Nothing to reset — inbox already clean.")
            return

        trackers = (
            await db.execute(
                select(PipelineFile).where(
                    PipelineFile.source_kind == "email",
                    PipelineFile.source_id.in_(ids),
                )
            )
        ).scalars().all()

        status_before = dict(
            (await db.execute(
                select(EmailMessage.status, func.count())
                .where(EmailMessage.provider_message_id.in_(ids))
                .group_by(EmailMessage.status)
            )).all()
        )

        print(f"  emails to reset: {len(ids)}")
        for st, n in sorted(status_before.items()):
            print(f"    status {st!r}: {n}")
        print(f"  email pipeline items to delete: {len(trackers)}")

        if args.dry_run:
            print("Dry run — no changes written.")
            return

        for t in trackers:
            purge_raw_copy(t)

        await db.execute(
            delete(PipelineFile).where(
                PipelineFile.source_kind == "email",
                PipelineFile.source_id.in_(ids),
            )
        )
        await db.execute(
            update(EmailMessage)
            .where(EmailMessage.provider_message_id.in_(ids))
            .values(
                status=EmailStatus.NEW,
                decided_at=None,
                no_sheets_found_at=None,
                no_sheets_note=None,
            )
        )
        await db.commit()

    print("Done — inbox reset to new. Reload Inbox / Activity log.")


if __name__ == "__main__":
    asyncio.run(main())
