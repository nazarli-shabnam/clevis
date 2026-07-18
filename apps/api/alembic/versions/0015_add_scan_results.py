"""add scan_results

Every /analytics/overview scan was previously a one-off snapshot with no
history — there was no way to see a security score's trend over time. This
adds a scan_results table that persists a row for every completed scan
(owner, score, check counts, and the full checks payload as JSON text,
matching the json-as-Text convention used by audit_logs.payload and
jobs.payload/result elsewhere in this schema), plus a composite index on
(owner, created_at) since every read of this table is "most recent N scans
for this owner".

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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_scan_results_owner_created_at", "scan_results", ["owner", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_results_owner_created_at", table_name="scan_results")
    op.drop_table("scan_results")
