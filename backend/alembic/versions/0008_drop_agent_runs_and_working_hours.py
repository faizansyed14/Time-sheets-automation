"""drop agent_runs tables and unused total_working_hours column

Revision ID: 0008_drop_agent_runs
Revises: 0007_drop_email_ai_check
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_drop_agent_runs"
down_revision = "0007_drop_email_ai_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_column("timesheet_records", "total_working_hours")


def downgrade() -> None:
    op.add_column(
        "timesheet_records",
        sa.Column("total_working_hours", sa.Float(), nullable=True),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("email_id", sa.String(), nullable=False),
        sa.Column("email_subject", sa.String(), nullable=True),
        sa.Column("email_sender", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("has_attachments", sa.Boolean(), nullable=False),
        sa.Column("relevant", sa.Boolean(), nullable=True),
        sa.Column("relevance_reason", sa.Text(), nullable=True),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("attachments_count", sa.Integer(), nullable=False),
        sa.Column("timesheet_count", sa.Integer(), nullable=False),
        sa.Column("leaves_count", sa.Integer(), nullable=False),
        sa.Column("approval", sa.String(), nullable=False),
        sa.Column("approval_source", sa.String(), nullable=True),
        sa.Column("employee_name", sa.String(), nullable=True),
        sa.Column("employee_id", sa.String(), nullable=True),
        sa.Column("staged_pipeline_ids", sa.JSON(), nullable=False),
        sa.Column("models_used", sa.JSON(), nullable=False),
        sa.Column("cost_estimate_usd", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_email_id", "agent_runs", ["email_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])
