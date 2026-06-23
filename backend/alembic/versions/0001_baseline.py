"""baseline schema — all tables as of the initial Alembic adoption

Creates the full current schema:
    auth_users, app_config, all_employee_data, email_messages,
    pipeline_files, timesheet_records

This is the post-`upgrade_v2` shape, i.e. it already includes
`timesheet_records.source_files` and the composite unique constraint
`uq_employee_id_name` on `all_employee_data(employee_id, name)`.

On a FRESH database:        alembic upgrade head        (creates everything)
On an EXISTING database
already built via create_all: alembic stamp 0001_baseline  (mark as applied)

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-22
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Postgres timestamp default that matches the models' `server_default=func.now()`.
_NOW = sa.text("now()")


def upgrade() -> None:
    # ---- all_employee_data (the authoritative employee matcher list) ----
    op.create_table(
        "all_employee_data",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("employee_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("dco_number", sa.String(), nullable=True),
        sa.Column("account_manager", sa.String(), nullable=True),
        sa.Column("employee_email_id", sa.String(), nullable=True),
        sa.Column("project", sa.String(), nullable=True),
        sa.Column("contact_no", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("all_emails", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_id", "name", name="uq_employee_id_name"),
    )
    op.create_index(op.f("ix_all_employee_data_employee_id"), "all_employee_data", ["employee_id"], unique=False)
    op.create_index(op.f("ix_all_employee_data_name"), "all_employee_data", ["name"], unique=False)

    # ---- app_config (admin-managed runtime key/value, secrets encrypted) ----
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(op.f("ix_app_config_category"), "app_config", ["category"], unique=False)

    # ---- auth_users (application users + RBAC + 2FA mode) ----
    op.create_table(
        "auth_users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("auth_mode", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_users_role"), "auth_users", ["role"], unique=False)
    op.create_index(op.f("ix_auth_users_username"), "auth_users", ["username"], unique=True)

    # ---- email_messages (inbox mirror + workflow state) ----
    op.create_table(
        "email_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider_message_id", sa.String(), nullable=False),
        sa.Column("sender_name", sa.String(), nullable=True),
        sa.Column("sender_email", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("has_approval_screenshot", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_messages_provider_message_id"), "email_messages", ["provider_message_id"], unique=True)
    op.create_index(op.f("ix_email_messages_status"), "email_messages", ["status"], unique=False)

    # ---- pipeline_files (audit trail of every file through extraction) ----
    op.create_table(
        "pipeline_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("attachment_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("employee_id", sa.String(), nullable=True),
        sa.Column("employee_name", sa.String(), nullable=True),
        sa.Column("month", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("record_id", sa.String(), nullable=True),
        sa.Column("raw_path", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pipeline_files_failure_code"), "pipeline_files", ["failure_code"], unique=False)
    op.create_index(op.f("ix_pipeline_files_record_id"), "pipeline_files", ["record_id"], unique=False)
    op.create_index(op.f("ix_pipeline_files_source_id"), "pipeline_files", ["source_id"], unique=False)
    op.create_index(op.f("ix_pipeline_files_source_kind"), "pipeline_files", ["source_kind"], unique=False)
    op.create_index(op.f("ix_pipeline_files_status"), "pipeline_files", ["status"], unique=False)

    # ---- timesheet_records (one employee's extracted leave data per month) ----
    op.create_table(
        "timesheet_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("extracted_employee_id", sa.String(), nullable=True),
        sa.Column("extracted_employee_name", sa.String(), nullable=True),
        sa.Column("matched_employee_pk", sa.String(), nullable=True),
        sa.Column("employee_id", sa.String(), nullable=True),
        sa.Column("employee_name", sa.String(), nullable=True),
        sa.Column("account_manager", sa.String(), nullable=True),
        sa.Column("dco_number", sa.String(), nullable=True),
        sa.Column("match_note", sa.String(), nullable=True),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("calendar_days", sa.Integer(), nullable=True),
        sa.Column("annual_leave_dates", sa.JSON(), nullable=False),
        sa.Column("remote_work_dates", sa.JSON(), nullable=False),
        sa.Column("sick_leave_dates", sa.JSON(), nullable=False),
        sa.Column("unpaid_leave_dates", sa.JSON(), nullable=False),
        sa.Column("absent_dates", sa.JSON(), nullable=False),
        sa.Column("public_holiday_dates", sa.JSON(), nullable=False),
        sa.Column("total_working_hours", sa.Float(), nullable=True),
        sa.Column("validation_status", sa.String(), nullable=False),
        sa.Column("llm_summary", sa.Text(), nullable=True),
        sa.Column("hr_flags", sa.JSON(), nullable=False),
        sa.Column("approval_detected", sa.Boolean(), nullable=False),
        sa.Column("approval_detail", sa.String(), nullable=True),
        sa.Column("approval_status", sa.String(), nullable=False),
        sa.Column("source_email_id", sa.String(), nullable=True),
        sa.Column("storage_folder", sa.String(), nullable=True),
        sa.Column("source_files", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_timesheet_records_approval_status"), "timesheet_records", ["approval_status"], unique=False)
    op.create_index(op.f("ix_timesheet_records_employee_id"), "timesheet_records", ["employee_id"], unique=False)
    op.create_index(op.f("ix_timesheet_records_employee_name"), "timesheet_records", ["employee_name"], unique=False)
    op.create_index(op.f("ix_timesheet_records_matched_employee_pk"), "timesheet_records", ["matched_employee_pk"], unique=False)
    op.create_index(op.f("ix_timesheet_records_month"), "timesheet_records", ["month"], unique=False)
    op.create_index(op.f("ix_timesheet_records_source_email_id"), "timesheet_records", ["source_email_id"], unique=False)
    op.create_index(op.f("ix_timesheet_records_validation_status"), "timesheet_records", ["validation_status"], unique=False)
    op.create_index(op.f("ix_timesheet_records_year"), "timesheet_records", ["year"], unique=False)


def downgrade() -> None:
    op.drop_table("timesheet_records")
    op.drop_table("pipeline_files")
    op.drop_table("email_messages")
    op.drop_table("auth_users")
    op.drop_table("app_config")
    op.drop_table("all_employee_data")
