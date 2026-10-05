"""Add orgs.score_hygiene_checks: a per-org override of the instance-wide hygiene-scoring setting.

Nullable boolean add with no backfill and no default: NULL means "follow the instance setting"
(``app_config.score_hygiene_checks``), which is exactly how every existing org behaves today, so
nothing changes until an org admin sets it. Downgrade drops the column (and any overrides stored).

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orgs", sa.Column("score_hygiene_checks", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "score_hygiene_checks")
