"""Drop unused app_config table — AI settings are .env-only now.

Revision ID: 0013_drop_app_config
Revises: 0012_email_conversation_id
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_drop_app_config"
down_revision = "0012_email_conversation_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(op.f("ix_app_config_category"), table_name="app_config")
    op.drop_table("app_config")


def downgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("category", sa.String(), nullable=False, server_default="general"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(op.f("ix_app_config_category"), "app_config", ["category"], unique=False)
