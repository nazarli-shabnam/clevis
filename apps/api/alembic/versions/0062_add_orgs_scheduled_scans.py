"""Add orgs.scheduled_scans: a per-org override of the instance-wide scheduled-scan cadence.

Nullable boolean add with no backfill and no default: NULL means "follow the instance setting"
(``app_config.scheduled_scan_cadence``, off unless an operator turns it on), which is exactly how every
existing org behaves today, so nothing changes until an operator or org admin sets something. Same
shape as ``orgs.score_hygiene_checks`` (0057). Downgrade drops the column (and any overrides stored).

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("scheduled_scans", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "scheduled_scans")
