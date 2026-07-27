"""Add working_dates / weekend_dates to timesheet_records.

Extraction already computes these (day-accounting completeness check) but
they only ever lived in PipelineFile.extraction_meta — gone the moment a
record is filed. Persisted here with the same shape/UI as the 7 leave
buckets so a reviewer can see and correct them on the Record page too.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017_timesheet_day_fields"
down_revision = "0016_email_thread_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "timesheet_records",
        sa.Column("working_dates", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "timesheet_records",
        sa.Column("weekend_dates", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("timesheet_records", "weekend_dates")
    op.drop_column("timesheet_records", "working_dates")
