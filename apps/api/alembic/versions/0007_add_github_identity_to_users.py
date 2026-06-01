"""add github identity columns to users; make password_hash nullable

GitHub OAuth sign-in (S1) links a local user to a GitHub account and creates users who have
no email/password credential, so password_hash becomes nullable and the user row carries the
GitHub identity (unique github_user_id, login, avatar).

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("github_user_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("github_login", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_users_github_user_id", "users", ["github_user_id"])
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
    op.drop_constraint("uq_users_github_user_id", "users", type_="unique")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "github_login")
    op.drop_column("users", "github_user_id")
