"""Add aco_number to all_employee_data.

Sits alongside the existing dco_number. Both are written into the employee's
File Vault folder name at filing time, so a vault folder identifies the person
by contract number rather than by name alone.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0022_employee_aco_number"
down_revision = "0021_employee_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("all_employee_data", sa.Column("aco_number", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("all_employee_data", "aco_number")
