"""Fix three column defaults that drifted from the models (found by ``compare_server_default``).

``alembic check`` did not compare server defaults, so these went unnoticed:

- ``jobs.status``: 0001 passed the string ``'queued'`` *with its own quotes* as the default, so the stored default
  is the literal text ``'queued'`` (quote characters included). A raw ``INSERT INTO jobs`` without ``status``
  would create a job the worker (``status = 'queued'``) never claims. Now the plain string ``queued``.
- ``users.email_verified``: 0018 made the default TRUE, but accounts start unverified (the model's Python
  default is False). A raw ``INSERT INTO users`` omitting it would create an already-verified account and skip
  the invitation-acceptance gate. Now FALSE.
- ``github_app_credentials.id``: 0087 declared a SmallInteger primary key, which Postgres gives a sequence
  default, but the row is pinned to ``id = 1`` by a CHECK and the model sets ``autoincrement=False``. The
  default is dropped (the sequence itself is left alone).

Schema change: column defaults only. No row is rewritten or altered: a default applies to future INSERTs that
omit the column. Before applying, an operator can check for jobs that already carry the quoted status:
``SELECT count(*) FROM jobs WHERE status <> btrim(status, chr(39))`` (the ORM always sets ``status``
explicitly, so this is expected to be 0). ``users`` rows are unaffected. Downgrade restores the old defaults.

Revision ID: 0092
Revises: 0091
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("jobs", "status", existing_type=sa.String(), existing_nullable=False, server_default=sa.text("'queued'"))
    op.alter_column("users", "email_verified", existing_type=sa.Boolean(), existing_nullable=False, server_default=sa.text("false"))
    op.alter_column("github_app_credentials", "id", existing_type=sa.SmallInteger(), existing_nullable=False, server_default=None)


def downgrade() -> None:
    op.alter_column(
        "github_app_credentials",
        "id",
        existing_type=sa.SmallInteger(),
        existing_nullable=False,
        server_default=sa.text("nextval('github_app_credentials_id_seq'::regclass)"),
    )
    op.alter_column("users", "email_verified", existing_type=sa.Boolean(), existing_nullable=False, server_default=sa.text("true"))
    op.alter_column("jobs", "status", existing_type=sa.String(), existing_nullable=False, server_default=sa.text("'''queued'''"))
