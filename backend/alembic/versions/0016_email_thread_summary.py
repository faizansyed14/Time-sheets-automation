"""Plain-English summary of what a conversation is actually about.

A timesheet thread can be a submission, a chase, a correction, an approval, or
a mix — and reading eight replies to find out is the slow part of a reviewer's
day. The summary is generated on demand from the PII-scrubbed bodies and kept
so it is not re-generated on every open.

Written on ONE row of the conversation (the message it was generated from) and
read back by conversation, so it is stored once rather than copied onto every
message. Preserved across inbox resync: _sync_message's ON CONFLICT DO UPDATE
lists the columns it refreshes, and this is not one of them.

Revision ID: 0016_email_thread_summary
Revises: 0015_email_extracted_sheets
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_email_thread_summary"
down_revision = "0015_email_extracted_sheets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("thread_summary", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_messages", "thread_summary")
