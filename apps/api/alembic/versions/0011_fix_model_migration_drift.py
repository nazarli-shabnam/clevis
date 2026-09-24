"""fix model/migration drift: NOT NULL timestamps, drop redundant saved_tokens index

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("audit_logs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("github_installations", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("jobs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("jobs", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.drop_index("ix_saved_tokens_org", table_name="saved_tokens")


def downgrade() -> None:
    op.create_index("ix_saved_tokens_org", "saved_tokens", ["org"])
    op.alter_column("jobs", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("jobs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("github_installations", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("audit_logs", "created_at", existing_type=sa.DateTime(timezone=True), nullable=True)
