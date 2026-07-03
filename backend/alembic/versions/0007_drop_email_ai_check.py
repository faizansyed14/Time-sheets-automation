"""drop inbox ai_check columns — replaced by the "Run Agents" pipeline

Revision ID: 0007_drop_email_ai_check
Revises: 0006_agent_runs
Create Date: 2026-07-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_drop_email_ai_check"
down_revision = "0006_agent_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("email_messages") as batch:
        batch.drop_column("ai_check")
        batch.drop_column("ai_checked_at")


def downgrade() -> None:
    with op.batch_alter_table("email_messages") as batch:
        batch.add_column(sa.Column("ai_check", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("ai_checked_at", sa.DateTime(timezone=True), nullable=True))
