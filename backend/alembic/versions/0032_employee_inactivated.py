"""Record WHEN an employee was marked inactive.

Dashboard and Export become month-wise: an inactive employee still shows for the
month they were inactivated (and earlier months) and drops off from the next.
`inactive_ym` is that month as year*100+month in UAE time (e.g. 202610).

Existing inactive rows are left NULL on purpose — their real month was never
recorded, so they only appear in months where they have data (a filed record or
a received sheet). Nothing is guessed.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0032_employee_inactivated"
down_revision = "0031_system_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("all_employee_data", sa.Column("inactivated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("all_employee_data", sa.Column("inactive_ym", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("all_employee_data", "inactive_ym")
    op.drop_column("all_employee_data", "inactivated_at")
