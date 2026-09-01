"""Add auth_users.last_seen_at — bumped (throttled) on every authenticated
request so the Users & Access page can show an accurate online/offline dot.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0029_user_last_seen"
down_revision = "0028_system_notice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_users", "last_seen_at")
