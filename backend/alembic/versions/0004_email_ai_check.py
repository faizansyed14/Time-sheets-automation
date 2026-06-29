"""email_messages: persist inbox AI check results

Revision ID: 0004_email_ai_check
Revises: 0003_auth_totp
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_email_ai_check"
down_revision = "0003_auth_totp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("ai_check", sa.JSON(), nullable=True))
    op.add_column("email_messages", sa.Column("ai_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("email_messages", "ai_checked_at")
    op.drop_column("email_messages", "ai_check")
