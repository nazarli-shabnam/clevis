"""Enforce NOT NULL on invitations/github_installations.tenant_id.

Re-runs each column's backfill UPDATE (idempotent) before adding NOT NULL, to catch any
row written before the dual-write path went live. Data-loss/backfill risk: if a real gap
remains beyond what the backfill can resolve, the ALTER COLUMN fails atomically and the
whole migration rolls back rather than silently succeeding with missing data.

orgs.tenant_id is deliberately not included and stays nullable permanently: a new org row
and its reciprocal tenant row each need the other's not-yet-existing id, and Postgres
NOT NULL can't be deferred the way a FK/UNIQUE/PK constraint can.

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text(
            "UPDATE invitations i SET tenant_id = t.id "
            "FROM tenants t WHERE t.org_id = i.org_id AND t.kind = 'org' AND i.tenant_id IS NULL"
        )
    )
    op.alter_column("invitations", "tenant_id", nullable=False)

    conn.execute(
        sa.text(
            "UPDATE github_installations gi SET tenant_id = t.id "
            "FROM tenants t "
            "WHERE gi.tenant_id IS NULL "
            "AND ((gi.org_id IS NOT NULL AND t.org_id = gi.org_id AND t.kind = 'org') "
            "OR (gi.owner_user_id IS NOT NULL AND t.personal_user_id = gi.owner_user_id AND t.kind = 'personal'))"
        )
    )
    op.alter_column("github_installations", "tenant_id", nullable=False)


def downgrade() -> None:
    op.alter_column("github_installations", "tenant_id", nullable=True)
    op.alter_column("invitations", "tenant_id", nullable=True)
