"""Backfill one personal tenant/membership per existing user.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("INSERT INTO tenants (kind, personal_user_id) SELECT 'personal', id FROM users"))
    conn.execute(
        sa.text(
            "INSERT INTO memberships (tenant_id, user_id, role) "
            "SELECT t.id, t.personal_user_id, 'admin' "
            "FROM tenants t WHERE t.kind = 'personal'"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM memberships WHERE tenant_id IN (SELECT id FROM tenants WHERE kind = 'personal')"
        )
    )
    conn.execute(sa.text("DELETE FROM tenants WHERE kind = 'personal'"))
