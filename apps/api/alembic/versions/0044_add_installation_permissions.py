"""Add github_installations.granted_permissions / permissions_synced_at.

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "github_installations",
        sa.Column("granted_permissions", JSONB(), nullable=True),
    )
    op.add_column(
        "github_installations",
        sa.Column("permissions_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("github_installations", "permissions_synced_at")
    op.drop_column("github_installations", "granted_permissions")
