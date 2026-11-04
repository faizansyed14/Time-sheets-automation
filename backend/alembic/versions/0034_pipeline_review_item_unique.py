"""Backstop against duplicate review items for the same thread + group.

Extract Email stages ONE pipeline_files row per (thread, employee+month tag).
The application serialises runs per thread (Redis lock) and looks the row up
before inserting, but two writers could still race in an edge case (lock
expiry, Redis down). This partial unique index makes a duplicate physically
impossible instead of merely unlikely; stage_groups handles the resulting
IntegrityError by re-reading the winner's row.

Only the Extract Email tag rows are covered (attachment_id begins with
'__email_extract__:'), so uploads/portal rows are untouched. If duplicates
already exist the index is NOT created (and nothing fails) - clean them up in
the Pipeline page and re-run the migration.
"""
from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa

revision = "0034_pipeline_review_unique"
down_revision = "0033_notice_ack"
branch_labels = None
depends_on = None

INDEX = "uq_pipeline_files_email_review_item"
TAG = "__email_extract__:%"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    dupes = bind.execute(sa.text(
        "SELECT count(*) FROM ("
        " SELECT 1 FROM pipeline_files"
        " WHERE source_kind = 'email' AND thread_key IS NOT NULL"
        "   AND attachment_id LIKE :tag"
        " GROUP BY source_kind, thread_key, attachment_id HAVING count(*) > 1) d"
    ), {"tag": TAG}).scalar_one()
    if dupes:
        logging.getLogger("alembic").warning(
            "%s duplicate review item group(s) in pipeline_files - unique index %s NOT created",
            dupes, INDEX)
        return
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX} ON pipeline_files "
        "(source_kind, thread_key, attachment_id) "
        f"WHERE source_kind = 'email' AND thread_key IS NOT NULL AND attachment_id LIKE '{TAG}'"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
