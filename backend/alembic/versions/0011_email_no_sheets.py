"""Track "Extract Email found nothing to stage" per email.

Revision ID: 0011_email_no_sheets
Revises: 0010_email_recipients
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_email_no_sheets"
down_revision = "0010_email_recipients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("no_sheets_found_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_messages", sa.Column("no_sheets_note", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_messages", "no_sheets_note")
    op.drop_column("email_messages", "no_sheets_found_at")
