"""Remove the manager portal role/login entirely — approval is now the
internal timesheet team's job via Compare & Fix (Accept, or a "Send back"
action with a note), not a separate manager login. Adds the employee's
mandatory approval self-attestation and drops the now-unused manager_name.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0024_portal_remove_manager"
down_revision = "0023_portal_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_submissions",
        sa.Column("approval_claimed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # "Accepted" is now represented by the computed review_state, never by
    # status="approved" (nothing writes that value anymore) — normalize any
    # rows from the old manager-approval flow back to "submitted" so they
    # read correctly under the new state machine.
    op.execute("UPDATE portal_submissions SET status = 'submitted' WHERE status = 'approved'")
    # No more manager logins — any that were created for testing are removed;
    # employee accounts (the only kind the app still creates) are untouched.
    op.execute("DELETE FROM portal_users WHERE role = 'manager'")
    op.drop_column("portal_users", "manager_name")


def downgrade() -> None:
    op.add_column("portal_users", sa.Column("manager_name", sa.String(), nullable=True))
    op.drop_column("portal_submissions", "approval_claimed")
