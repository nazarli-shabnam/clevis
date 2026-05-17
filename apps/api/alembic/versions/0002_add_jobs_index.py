"""add composite index on jobs (status, job_type)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-14
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_jobs_status_job_type", "jobs", ["status", "job_type"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status_job_type", table_name="jobs")
