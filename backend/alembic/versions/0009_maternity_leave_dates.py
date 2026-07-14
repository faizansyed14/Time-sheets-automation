"""Add maternity_leave_dates bucket to timesheet_records."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_maternity_leave_dates"
down_revision = "0008_drop_agent_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "timesheet_records",
        sa.Column("maternity_leave_dates", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("timesheet_records", "maternity_leave_dates")
