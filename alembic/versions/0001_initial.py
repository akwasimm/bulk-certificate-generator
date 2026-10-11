"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

job_status = sa.Enum(
    "queued",
    "processing",
    "completed",
    "completed_with_failures",
    "failed",
    name="jobstatus",
    native_enum=False,
)
certificate_status = sa.Enum(
    "pending",
    "generated",
    "failed",
    name="certificatestatus",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "certificate_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("status", job_status, nullable=False),
        sa.Column("course_name", sa.String(length=200), nullable=False),
        sa.Column("issued_by", sa.String(length=160), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("total_recipients", sa.Integer(), nullable=False),
        sa.Column("processed_recipients", sa.Integer(), nullable=False),
        sa.Column("succeeded_recipients", sa.Integer(), nullable=False),
        sa.Column("failed_recipients", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_certificate_jobs_status", "certificate_jobs", ["status"])

    op.create_table(
        "certificates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("certificate_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient_index", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("status", certificate_status, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_certificates_job_id", "certificates", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_certificates_job_id", table_name="certificates")
    op.drop_table("certificates")
    op.drop_index("ix_certificate_jobs_status", table_name="certificate_jobs")
    op.drop_table("certificate_jobs")
