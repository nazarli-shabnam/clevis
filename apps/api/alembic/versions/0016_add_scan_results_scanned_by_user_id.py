"""add scan_results.scanned_by_user_id

The new GET .../analytics/history endpoints read scan_results by a raw
`owner` string with no other scoping. For the org-scoped endpoint that's
fine (require_org_role already gates on membership), but the personal
endpoint (GET /me/analytics/history?owner=...) had no such gate at all --
any authenticated user could read any owner's persisted score history
just by naming it, regardless of whether they have any access to that
owner's GitHub data. A personal scan against an owner with no matching
workspace Org (the common raw-PAT-paste flow, never installing the
GitHub App) has no membership row to check against, so there was no way
to scope personal-history reads without recording who ran the scan.

This column tracks that: set on insert for personal scans only (org-scoped
scans leave it null and rely on org membership at read time instead).
Nullable and additive-only -- no backfill, no data-loss risk; existing
rows (none exist yet in production since scan_results itself just shipped)
simply have no operator to test the tighter permission,
which is a slightly stricter default (deny) not a looser one.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scan_results", sa.Column("scanned_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("scan_results", "scanned_by_user_id")
