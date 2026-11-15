"""Add delivery health to notification_destinations.

A webhook that starts returning 404/410 or a broken SMTP setup used to fail silently: the only trace was a
``notification.sent`` audit row with ``ok: false``. The send path now records the outcome of each delivery on the
destination itself so Settings can show a "failing" badge with the last error.

Schema change: four columns added to ``notification_destinations``, all nullable or defaulted, so no existing row
is rewritten in a way that loses data and nothing is backfilled (a destination reads as "never attempted" until its
next delivery). ``last_status`` is ``ok`` or ``failed``; ``last_error`` is the short, already-audited failure reason
(never the URL), truncated by the writer; ``consecutive_failures`` resets to 0 on a success. No new grants or RLS: the
table's existing policy and the API role's table privileges already cover new columns. Downgrade drops the columns
(the health information is lost; nothing else depends on it).

Revision ID: 0087
Revises: 0086
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_destinations", sa.Column("last_status", sa.String(), nullable=True))
    op.add_column("notification_destinations", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("notification_destinations", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "notification_destinations",
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    for column in ("consecutive_failures", "last_attempt_at", "last_error", "last_status"):
        op.drop_column("notification_destinations", column)
