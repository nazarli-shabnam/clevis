"""Make scan_results.score nullable.

A scan with no scored checks (a personal account with no repos, where the MFA check and every repo check are
``not_applicable``) assessed nothing, so it now stores ``score = NULL`` instead of a perfect 100 that fed the
badge, the score API, alerts and the "fully compliant" count. Dropping NOT NULL is metadata-only and rewrites
no rows; existing rows keep their score.

Downgrade restores NOT NULL, which needs a value for any NULL rows written meanwhile: they are set to 100,
the pre-0064 behaviour, so that is lossy (the "nothing was assessed" marker is gone) but never fails.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("scan_results", "score", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE scan_results SET score = 100 WHERE score IS NULL")
    op.alter_column("scan_results", "score", existing_type=sa.Integer(), nullable=False)
