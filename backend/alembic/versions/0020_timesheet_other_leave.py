"""Add other_leave_dates to timesheet_records.

A new leave bucket for named leave types with no dedicated column of their
own — mourning leave, paternity leave — same shape/UI as the other 7 leave
buckets so a reviewer can see and correct them on the Record page too.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0020_timesheet_other_leave"
down_revision = "0019_extraction_debug_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "timesheet_records",
        sa.Column("other_leave_dates", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("timesheet_records", "other_leave_dates")
