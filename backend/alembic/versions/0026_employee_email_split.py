"""Add work_email / personal_email to all_employee_data, and drop the old
all_emails blob they replace. A source sheet that lists a company address
and a personal one (AUH's "WORK EMAIL"/"PERSONAL EMAIL" columns) must never
have them silently merged into one — which is exactly what all_emails did,
and the only reason it existed. employee_email_id (the resolved "primary"
address) stays — it's still what most callers just need a single usable
address from; only Reminders reads work_email/personal_email directly, so a
sender can choose which one to actually use.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0026_employee_email_split"
down_revision = "0025_reminder_service"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("all_employee_data", sa.Column("work_email", sa.String(), nullable=True))
    op.add_column("all_employee_data", sa.Column("personal_email", sa.String(), nullable=True))
    op.drop_column("all_employee_data", "all_emails")


def downgrade() -> None:
    op.add_column("all_employee_data", sa.Column("all_emails", sa.String(), nullable=True))
    op.drop_column("all_employee_data", "personal_email")
    op.drop_column("all_employee_data", "work_email")
