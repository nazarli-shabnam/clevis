"""Add scan_results (tenant_id, created_at DESC) index for the tenant-scoped latest-scan reads.

The public badge (``public_badge_score``, 0049) and ``scan_results_repo.latest_with_checks`` both
filter on ``tenant_id`` and order by ``created_at DESC``; the only existing index is
``(owner, created_at)``, which neither can use because they match ``lower(owner)``. With this index
the planner walks one tenant's scans newest-first and applies the ``lower(owner)`` match as a filter.

Index-only change: no data is touched. Numbered 0053 because open PRs already claim 0051/0052;
``down_revision`` is the current head (0050).

Revision ID: 0053
Revises: 0050
Create Date: 2026-10-05
"""

from alembic import op

revision = "0053"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_scan_results_tenant_id_created_at ON scan_results (tenant_id, created_at DESC)")


def downgrade() -> None:
    op.drop_index("ix_scan_results_tenant_id_created_at", table_name="scan_results")
