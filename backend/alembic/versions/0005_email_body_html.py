"""email_messages: add body_html column for rich email rendering

Revision ID: 0005_email_body_html
Revises: 0004_email_ai_check
Create Date: 2026-06-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_email_body_html"
down_revision = "0004_email_ai_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_messages", sa.Column("body_html", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_messages", "body_html")
