"""Add orgs.target_score (#1278).

One additive nullable column: the security score an org wants to stay at or above (1-100, validated by the API).
NULL = no target, which is every existing org, so there is no backfill and nothing changes on deploy. ``orgs`` is
already readable and writable by ``clevis_api`` at table level, so no grant is needed; the worker's narrow column
grants on ``orgs`` do not include it and it never reads it.

Downgrade drops the column: the configured targets are lost.

Revision ID: 0097
Revises: 0096
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("target_score", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "target_score")
