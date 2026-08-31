"""Add reminder_config, reminder_runs, reminder_logs — the timesheet reminder
service (automatic 28th/9am UAE run + per-employee "Send now" + test sends).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0025_reminder_service"
down_revision = "0024_portal_remove_manager"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminder_config",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("auto_send_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
    )

    op.create_table(
        "reminder_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("triggered_by", sa.String(), nullable=True),
    )
    op.create_index("ix_reminder_runs_trigger", "reminder_runs", ["trigger"])

    op.create_table(
        "reminder_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("employee_pk", sa.String(), nullable=True),
        sa.Column("employee_id", sa.String(), nullable=True),
        sa.Column("employee_name", sa.String(), nullable=True),
        sa.Column("recipient_email", sa.String(), nullable=True),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reminder_logs_run_id", "reminder_logs", ["run_id"])
    op.create_index("ix_reminder_logs_employee_pk", "reminder_logs", ["employee_pk"])
    op.create_index("ix_reminder_logs_month", "reminder_logs", ["month"])
    op.create_index("ix_reminder_logs_year", "reminder_logs", ["year"])
    op.create_index("ix_reminder_logs_trigger", "reminder_logs", ["trigger"])
    op.create_index("ix_reminder_logs_status", "reminder_logs", ["status"])
    # The hot path (dedup check + "last sent" lookup) always filters by these
    # three together — one composite index instead of relying on the three
    # single-column ones above to be combined efficiently.
    op.create_index(
        "ix_reminder_logs_employee_month_year",
        "reminder_logs", ["employee_pk", "month", "year"],
    )


def downgrade() -> None:
    op.drop_table("reminder_logs")
    op.drop_table("reminder_runs")
    op.drop_table("reminder_config")
