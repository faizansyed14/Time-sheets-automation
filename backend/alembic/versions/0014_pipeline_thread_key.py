"""Key Extract Email review items to the CONVERSATION, not one message.

Extract Email reads the whole thread in one model call, so the unit of work is
the conversation. Keeping the dedupe key on `source_id` (a single message id)
meant a reply arriving later produced a SECOND review item for the same
employee+month, and a second stored copy of an ever-growing thread.

`thread_key` holds the conversation id. Staging dedupes on it, so re-extracting
after a new reply UPDATES the existing item instead of duplicating it.
`source_id` is left alone — retries, "mark email ingested" and
TimesheetRecord.source_email_id all still need the originating message id.

Existing rows are backfilled from the email they came from, so items staged
before this migration dedupe against new runs instead of stranding a duplicate.

Revision ID: 0014_pipeline_thread_key
Revises: 0013_drop_app_config
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_pipeline_thread_key"
down_revision = "0013_drop_app_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_files", sa.Column("thread_key", sa.String(), nullable=True))
    op.create_index("ix_pipeline_files_thread_key", "pipeline_files", ["thread_key"])

    # Backfill: conversation id where the source email has one, else the
    # message id (a singleton thread of one message).
    op.execute(
        """
        UPDATE pipeline_files AS p
           SET thread_key = COALESCE(e.conversation_id, p.source_id)
          FROM email_messages AS e
         WHERE p.source_kind = 'email'
           AND p.source_id = e.provider_message_id
           AND p.thread_key IS NULL
        """
    )
    # Email items whose source row is gone: fall back to the message id.
    op.execute(
        """
        UPDATE pipeline_files
           SET thread_key = source_id
         WHERE source_kind = 'email' AND thread_key IS NULL AND source_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_files_thread_key", table_name="pipeline_files")
    op.drop_column("pipeline_files", "thread_key")
