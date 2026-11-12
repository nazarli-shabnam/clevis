"""Add orgs.digest_enabled and orgs.digest_recipients: an org's own leadership-digest settings.

``digest_enabled`` follows the nullable-override pattern of ``orgs.scheduled_scans`` (0062) and
``orgs.score_hygiene_checks`` (0057): NULL follows the instance-wide ``digest_cadence``, true sends the
digest even when the instance cadence is off, false opts the org out. ``digest_recipients`` is the org
admin's chosen recipient list (JSON array of lower-cased emails, each re-checked against the org's verified
members when the digest is sent); NULL keeps the default of every verified org admin.

Schema change: two nullable columns, no default, no backfill, so every existing org keeps behaving exactly
as before. Downgrade drops both columns (the per-org choices are lost).

Revision ID: 0083
Revises: 0082
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("digest_enabled", sa.Boolean(), nullable=True))
    op.add_column("orgs", sa.Column("digest_recipients", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "digest_recipients")
    op.drop_column("orgs", "digest_enabled")
