"""Add github_installations.tenant_id, backfilled, nullable.

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "github_installations", sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True)
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE github_installations gi SET tenant_id = t.id "
            "FROM tenants t "
            "WHERE (gi.org_id IS NOT NULL AND t.org_id = gi.org_id AND t.kind = 'org') "
            "OR (gi.owner_user_id IS NOT NULL AND t.personal_user_id = gi.owner_user_id AND t.kind = 'personal')"
        )
    )


def downgrade() -> None:
    op.drop_column("github_installations", "tenant_id")
