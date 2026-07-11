"""fix model/migration drift: NOT NULL timestamps, drop redundant saved_tokens index

`alembic check` (added to CI in this same change) flagged pre-existing drift between the
SQLAlchemy models and the schema actually produced by earlier migrations:

- audit_logs.created_at, github_installations.created_at, jobs.created_at, and
  jobs.updated_at are declared as non-nullable (`Mapped[datetime]`) in db.py, but
  0001_initial_schema.py created them without NOT NULL. All existing rows have a value
  (the columns have always had server_default=now() and nothing sets them to NULL), so
  this is a safe, non-destructive tightening.
- saved_tokens.org already has a UNIQUE constraint (0003_add_saved_tokens.py), which
  Postgres backs with its own implicit unique index; the separate explicit
  ix_saved_tokens_org index created in that same migration is redundant and has no
  corresponding `index=True` in the model, so alembic wants it gone.

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
