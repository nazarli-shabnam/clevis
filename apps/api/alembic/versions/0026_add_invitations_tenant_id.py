"""Add invitations.tenant_id, backfilled, nullable.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invitations", sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True))
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE invitations i SET tenant_id = t.id "
            "FROM tenants t WHERE t.org_id = i.org_id AND t.kind = 'org'"
        )
    )


def downgrade() -> None:
    op.drop_column("invitations", "tenant_id")
