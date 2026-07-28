"""Add extraction_debug_runs — a temporary, purgeable full trace of one
Extract Email/Upload run: raw LLM prompts/responses per pass-1/pass-2 call,
dropped/noise items, and the unabridged final sheets JSON. Everything the
live SSE progress stream shows but never persists (progress.py's emit() is
in-memory, per-request only) — for debugging extraction/prompt quality
while testing, meant to be bulk-deleted once done.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0019_extraction_debug_runs"
down_revision = "0018_month_calendar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_debug_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column("source_kind", sa.String(), nullable=True),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("thread_key", sa.String(), nullable=True, index=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("pipeline_file_id", sa.String(), nullable=True, index=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reused_sheets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pass1_calls", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("pass2_calls", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("dropped_items", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("triage", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("sheets", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("errors", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_table("extraction_debug_runs")
