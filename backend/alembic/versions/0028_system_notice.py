"""Add system_notice table — a single-row banner message + on/off toggle that
admin controls and every authenticated role (including viewer/vault_matcher)
can read. Singleton row, same pattern as reminder_config.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0028_system_notice"
down_revision = "0027_reminder_email_preference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_notice",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("message", sa.String(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("system_notice")
