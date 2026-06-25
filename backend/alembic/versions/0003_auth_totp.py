"""auth_users: TOTP authenticator secret + enrollment flag

Revision ID: 0003_auth_totp
Revises: 0002_pipeline_provenance
Create Date: 2026-06-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_auth_totp"
down_revision = "0002_pipeline_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_users", sa.Column("totp_secret_enc", sa.String(), nullable=True))
    op.add_column(
        "auth_users",
        sa.Column("totp_enrolled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("auth_users", "totp_enrolled")
    op.drop_column("auth_users", "totp_secret_enc")
