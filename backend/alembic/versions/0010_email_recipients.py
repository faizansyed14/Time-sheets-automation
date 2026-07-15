"""Add to/cc recipients on email messages.

Revision ID: 0010_email_recipients
Revises: 0009_maternity_leave_dates
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_email_recipients"
down_revision = "0009_maternity_leave_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("to_recipients", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("email_messages", sa.Column("cc_recipients", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("email_messages", "cc_recipients")
    op.drop_column("email_messages", "to_recipients")

