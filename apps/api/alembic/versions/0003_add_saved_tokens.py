"""add saved_tokens table

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_tokens",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("org", sa.String, nullable=False, unique=True),
        sa.Column("label", sa.String, nullable=True),
        sa.Column("encrypted_token", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_saved_tokens_org", "saved_tokens", ["org"])


def downgrade() -> None:
    op.drop_index("ix_saved_tokens_org", table_name="saved_tokens")
    op.drop_table("saved_tokens")
