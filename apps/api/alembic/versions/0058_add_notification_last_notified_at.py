"""Add notification_destinations.last_notified_at (alert cooldown / concurrent-scan de-duplication).

A nullable timestamptz column add: no backfill, no default, no data risk. Every existing
destination starts with NULL (= never alerted), so the first drop after deploy fires as before.
The API claims a destination with a conditional UPDATE on this column before messaging it, so two
concurrent scans (or a flapping score) alert once per cooldown window. ``clevis_api`` already holds
table-level UPDATE on ``notification_destinations`` (0048), so no new grant is needed.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_destinations", sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("notification_destinations", "last_notified_at")
