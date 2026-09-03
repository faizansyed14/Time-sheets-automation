"""Add system_health_check table — one row per monitored component ("llm",
"graph") tracking its current status so a scheduled check can alert on
state CHANGES (working -> broken) instead of spamming every tick.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0031_system_health"
down_revision = "0030_chat_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_health_check",
        sa.Column("component", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("system_health_check")
