"""Add portal_users, portal_submissions, portal_submission_files — the
employee/manager self-service timesheet portal. Deliberately separate from
auth_users (own login, own JWT namespace); portal_submissions/files feed the
SAME pipeline_files/ingest_manual_entry choke point every other intake path
already uses, via source_kind="portal".
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0023_portal_tables"
down_revision = "0022_employee_aco_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),           # "employee" | "manager"
        sa.Column("employee_pk", sa.String(), nullable=True),     # employee logins -> all_employee_data.id
        sa.Column("manager_name", sa.String(), nullable=True),    # manager logins -> matches Employee.account_manager
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_portal_users_username", "portal_users", ["username"], unique=True)
    op.create_index("ix_portal_users_employee_pk", "portal_users", ["employee_pk"])
    op.create_index("ix_portal_users_manager_name", "portal_users", ["manager_name"])

    op.create_table(
        "portal_submissions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("employee_pk", sa.String(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("manager_decision", sa.String(), nullable=False, server_default="pending"),
        sa.Column("manager_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("employee_note", sa.Text(), nullable=True),
        sa.Column("extraction_state", sa.String(), nullable=False, server_default="not_started"),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        # Computed-on-read caches (see ingestion note in portal_submission.py) —
        # nullable, populated by the status endpoints, never by Accept itself.
        sa.Column("review_state", sa.String(), nullable=True),
        sa.Column("record_id", sa.String(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_portal_submissions_employee_pk", "portal_submissions", ["employee_pk"])
    op.create_unique_constraint(
        "uq_portal_submissions_employee_month_year", "portal_submissions",
        ["employee_pk", "month", "year"])

    op.create_table(
        "portal_submission_files",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("submission_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),           # timesheet | sick_leave | other
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("stored_path", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_portal_submission_files_submission_id", "portal_submission_files", ["submission_id"])
    op.create_unique_constraint(
        "uq_portal_submission_files_submission_kind", "portal_submission_files",
        ["submission_id", "kind"])


def downgrade() -> None:
    op.drop_table("portal_submission_files")
    op.drop_table("portal_submissions")
    op.drop_table("portal_users")
