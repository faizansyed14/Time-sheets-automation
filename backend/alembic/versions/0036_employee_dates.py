"""Employee joining date.

Without a joining date, someone added in October counted in September's (and every
earlier month's) headcount and "missing" list. NULL means "since always" - every
existing row stays exactly as it was, so no past month changes when this is deployed.
The left date needs no column: it is the existing `inactivated_at` (0032).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0036_employee_dates"
down_revision = "0035_timesheet_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("all_employee_data", sa.Column("joining_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("all_employee_data", "joining_date")
