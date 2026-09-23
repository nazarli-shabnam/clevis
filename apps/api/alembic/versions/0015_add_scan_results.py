"""add scan_results

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scan_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("total_checks", sa.Integer(), nullable=False),
        sa.Column("failed_checks", sa.Integer(), nullable=False),
        sa.Column("checks_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_scan_results_owner_created_at", "scan_results", ["owner", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_results_owner_created_at", table_name="scan_results")
    op.drop_table("scan_results")
