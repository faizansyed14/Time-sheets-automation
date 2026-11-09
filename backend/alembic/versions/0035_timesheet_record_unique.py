"""One monthly record per matched employee + month.

Accept always MERGES into the existing record for that employee and month
(ingestion._find_existing), and filing is serialised per employee + month by a
lock. This partial unique index is the last line of defence: should two writers
ever slip past the lock, the database refuses the second record instead of
silently keeping two for the same month.

Only rows with a matched employee are covered. If duplicates already exist the
index is NOT created (nothing fails) - merge them, then re-run the migration.
"""
from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa

revision = "0035_timesheet_unique"
down_revision = "0034_pipeline_review_unique"
branch_labels = None
depends_on = None

INDEX = "uq_timesheet_records_employee_month"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    dupes = bind.execute(sa.text(
        "SELECT count(*) FROM ("
        " SELECT 1 FROM timesheet_records WHERE matched_employee_pk IS NOT NULL"
        " GROUP BY matched_employee_pk, month, year HAVING count(*) > 1) d"
    )).scalar_one()
    if dupes:
        logging.getLogger("alembic").warning(
            "%s duplicate employee+month group(s) in timesheet_records - unique index %s NOT created",
            dupes, INDEX)
        return
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON timesheet_records "
        "(matched_employee_pk, month, year) WHERE matched_employee_pk IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
