"""Remember which attachments have already been extracted, per message.

Extract Email re-reads a whole conversation on every run. A thread that gains
a reply would otherwise re-upload and re-read the same attachments it already
understood — and because replies commonly arrive a MONTH or two later, a
short-lived cache would be expired virtually every time it was consulted.

So this is stored durably, next to the message the attachments belong to:

    {"<sha256 of the file bytes>": {"filename": ..., "sheet": {...extraction}}}

Keyed by content, so a file edited and re-sent is correctly treated as new.
Written on the message rather than in a side table because it shares that
row's lifetime exactly, and because the inbox view needs it per message to
mark each attachment "Extracted" or "New".

Safe across resync: _sync_message's ON CONFLICT DO UPDATE lists the columns it
refreshes, and this is not one of them.

Revision ID: 0015_email_extracted_sheets
Revises: 0014_pipeline_thread_key
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_email_extracted_sheets"
down_revision = "0014_pipeline_thread_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_messages",
        sa.Column("extracted_sheets", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_messages", "extracted_sheets")
