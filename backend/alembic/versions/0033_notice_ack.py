"""System notice acknowledgements — one JSON column on the existing row.

`system_notice.acked_by` holds {user_id: ISO time} of everyone who clicked OK on
the current announcement. It is reset whenever the announcement changes, so no
separate table or version number is needed.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0033_notice_ack"
down_revision = "0032_employee_inactivated"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "system_notice",
        sa.Column("acked_by", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("system_notice", "acked_by")
