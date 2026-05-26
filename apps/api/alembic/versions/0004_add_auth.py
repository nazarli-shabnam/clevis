"""add users and app_config tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("is_owner", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "app_config",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Seed defaults — these are the only accepted config keys
    op.execute(
        sa.text(
            "INSERT INTO app_config (key, value) VALUES "
            "('github_api_base', 'https://api.github.com'), "
            "('cors_origins', '[\"*\"]'), "
            "('worker_poll_seconds', '5'), "
            "('debug', 'false')"
        )
    )


def downgrade() -> None:
    op.drop_table("app_config")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
