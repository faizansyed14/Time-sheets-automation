"""Add active flag to all_employee_data.

Replaces hard-delete on the Employee Matcher page: an employee can now be
marked inactive instead, which keeps their row (and every timesheet record /
vault file that references it) intact while excluding them from active
headline counts and coverage going forward.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0021_employee_active"
down_revision = "0020_timesheet_other_leave"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "all_employee_data",
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    op.drop_column("all_employee_data", "active")
