"""agent runs + steps (agentic email processing with relevance gate)

Revision ID: 0006_agent_runs
Revises: 0005_email_body_html
Create Date: 2026-07-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_agent_runs"
down_revision = "0005_email_body_html"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email_id", sa.String(), nullable=False, index=True),
        sa.Column("email_subject", sa.String(), nullable=True),
        sa.Column("email_sender", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="running", index=True),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("relevant", sa.Boolean(), nullable=True),
        sa.Column("relevance_reason", sa.Text(), nullable=True),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("attachments_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timesheet_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("leaves_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approval", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("approval_source", sa.String(), nullable=True),
        sa.Column("employee_name", sa.String(), nullable=True),
        sa.Column("employee_id", sa.String(), nullable=True),
        sa.Column("staged_pipeline_ids", sa.JSON(), nullable=True),
        sa.Column("models_used", sa.JSON(), nullable=True),
        sa.Column("cost_estimate_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=False, index=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("agent", sa.String(), nullable=False, server_default="manager"),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
