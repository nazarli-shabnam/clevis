"""Backfill one tenants/memberships row per existing org/org_membership.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("INSERT INTO tenants (kind, org_id) SELECT 'org', id FROM orgs"))
    conn.execute(
        sa.text(
            "INSERT INTO memberships (tenant_id, user_id, role) "
            "SELECT t.id, om.user_id, om.role "
            "FROM org_memberships om "
            "JOIN tenants t ON t.org_id = om.org_id AND t.kind = 'org'"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM memberships WHERE tenant_id IN (SELECT id FROM tenants WHERE kind = 'org')"
        )
    )
    conn.execute(sa.text("DELETE FROM tenants WHERE kind = 'org'"))
