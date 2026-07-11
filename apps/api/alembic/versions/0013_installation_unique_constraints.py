"""Unique constraints for github_installations upsert keys.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_github_installations_org_account",
        "github_installations",
        ["org_id", "account_login"],
        unique=True,
        postgresql_where=sa.text("org_id IS NOT NULL"),
    )
    op.create_index(
        "uq_github_installations_user_account",
        "github_installations",
        ["owner_user_id", "account_login"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_github_installations_user_account", table_name="github_installations")
    op.drop_index("uq_github_installations_org_account", table_name="github_installations")
