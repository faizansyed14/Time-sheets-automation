"""Add reminder_config.email_preference ("work" | "personal") — a global
choice of which of an employee's two separate addresses reminders send to,
now that work_email/personal_email are never blended into one value.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0027_reminder_email_preference"
down_revision = "0026_employee_email_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reminder_config",
        sa.Column("email_preference", sa.String(), nullable=False, server_default="work"),
    )


def downgrade() -> None:
    op.drop_column("reminder_config", "email_preference")
