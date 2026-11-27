"""Add orgs.scoring_excluded_repos (#1276).

One additive nullable column: repository names or ``*``/``?`` globs an org admin leaves out of scoring
(archived forks, sandboxes, test repos). NULL = none, which is every existing org's behaviour, so there is no
backfill and no existing score changes. ``orgs`` is already readable and writable by ``clevis_api`` at table
level, so no grant is needed; the worker's narrow column grants on ``orgs`` do not include it and it never reads it.

Downgrade drops the column: the configured exclusions are lost.

Revision ID: 0096
Revises: 0095
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("scoring_excluded_repos", sa.ARRAY(sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "scoring_excluded_repos")
