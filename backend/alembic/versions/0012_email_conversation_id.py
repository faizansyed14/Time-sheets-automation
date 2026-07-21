"""Group email messages into Outlook-style conversation threads.

Revision ID: 0012_email_conversation_id
Revises: 0011_email_no_sheets
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_email_conversation_id"
down_revision = "0011_email_no_sheets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("conversation_id", sa.String(), nullable=True))
    op.create_index("ix_email_messages_conversation_id", "email_messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_email_messages_conversation_id", table_name="email_messages")
    op.drop_column("email_messages", "conversation_id")
