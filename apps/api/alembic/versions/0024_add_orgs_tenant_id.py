"""Add orgs.tenant_id, backfilled, nullable.

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("tenant_id", sa.Integer(), nullable=True))
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE orgs SET tenant_id = t.id "
            "FROM tenants t WHERE t.org_id = orgs.id AND t.kind = 'org'"
        )
    )
    op.create_foreign_key(
        "fk_orgs_tenant_id_reciprocal",
        "orgs",
        "tenants",
        ["tenant_id", "id"],
        ["id", "org_id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_orgs_tenant_id_reciprocal", "orgs", type_="foreignkey")
    op.drop_column("orgs", "tenant_id")
