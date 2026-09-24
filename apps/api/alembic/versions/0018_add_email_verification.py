"""add users.email_verified/email_verify_token/email_verify_token_expires_at

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("users", sa.Column("email_verify_token", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("email_verify_token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_users_email_verify_token", "users", ["email_verify_token"])


def downgrade() -> None:
    op.drop_constraint("uq_users_email_verify_token", "users", type_="unique")
    op.drop_column("users", "email_verify_token_expires_at")
    op.drop_column("users", "email_verify_token")
    op.drop_column("users", "email_verified")
