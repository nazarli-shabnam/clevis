"""add invitations.expires_at

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invitations", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text("UPDATE invitations SET expires_at = created_at + interval '7 days' WHERE expires_at IS NULL")
    )
    op.alter_column("invitations", "expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    op.drop_column("invitations", "expires_at")
