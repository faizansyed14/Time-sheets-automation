"""pipeline_files: extraction provenance (model / method / used_ocr)

Adds three nullable columns to `pipeline_files` so the pipeline tracker can show,
per file, HOW it was read — which GPT model (gpt-4o vs the cheaper gpt-4o-mini),
whether a no-LLM deterministic/mock path was used, and whether the local OCR
reader produced the text layer. Purely additive; safe on existing data.

    extraction_model   VARCHAR  NULL   e.g. "gpt-4o", "gpt-4o-mini" (NULL = no LLM)
    extraction_method  VARCHAR  NULL   "vision-llm" | "deterministic-text" |
                                       "mock" | "manual" | "unsupported"
    used_ocr           BOOLEAN  NOT NULL DEFAULT false
    extraction_meta    JSON     NULL   render DPI, image detail, page count,
                                       OCR provider/status, text-layer, validation
                                       model, embedded .eml attachment, …

Revision ID: 0002_pipeline_provenance
Revises: 0001_baseline
Create Date: 2026-06-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_pipeline_provenance"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pipeline_files", sa.Column("extraction_model", sa.String(), nullable=True))
    op.add_column("pipeline_files", sa.Column("extraction_method", sa.String(), nullable=True))
    op.add_column(
        "pipeline_files",
        sa.Column("used_ocr", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("pipeline_files", sa.Column("extraction_meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("pipeline_files", "extraction_meta")
    op.drop_column("pipeline_files", "used_ocr")
    op.drop_column("pipeline_files", "extraction_method")
    op.drop_column("pipeline_files", "extraction_model")
