"""add scan_results.scanned_by_user_id

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
