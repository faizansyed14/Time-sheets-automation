"""Add chat_access_config table — a single-row on/off toggle admin controls
for whether non-admin users ("others") can see and use Ask AI. Singleton
row, same pattern as system_notice/reminder_config.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0030_chat_access"
down_revision = "0029_user_last_seen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_access_config",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("enabled_for_others", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("chat_access_config")
