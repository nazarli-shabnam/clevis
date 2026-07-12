"""Add jobs.retry_count for stale-processing reclaim cap.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("jobs", "retry_count")
