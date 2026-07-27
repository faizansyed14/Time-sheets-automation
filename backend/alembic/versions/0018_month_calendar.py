"""Add month_calendars — admin-configured weekends + public holidays per
(month, year), fed into Pass 2 as ground truth instead of the model having
to infer weekday-of-month/holidays itself.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0018_month_calendar"
down_revision = "0017_timesheet_day_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "month_calendars",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("weekend_weekdays", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("public_holidays", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("month", "year", name="uq_month_calendars_month_year"),
    )


def downgrade() -> None:
    op.drop_table("month_calendars")
