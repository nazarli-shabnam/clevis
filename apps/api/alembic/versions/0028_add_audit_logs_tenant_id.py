"""Add audit_logs.tenant_id, nullable, no backfill.

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "tenant_id")
