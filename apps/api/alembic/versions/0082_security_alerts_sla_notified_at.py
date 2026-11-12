"""Add security_alerts.sla_notified_at: when a Dependabot alert's SLA breach was announced.

The API's SLA-breach sweep stamps this before messaging ``sla_breach`` alert destinations, so each breach
is announced at most once (a restart or a second replica never repeats it). Like ``alert_notified_at`` the
stamp means "processed", not "delivered".

Schema change: one nullable column, no index (the sweep filters on the already-indexed tenant and state).
Existing open alerts are stamped at upgrade time, so rolling this out does not announce every alert that is
already past its SLA in one burst (the in-app feed and the burn-down page still show them). Downgrade drops
the column.

Revision ID: 0082
Revises: 0081
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("security_alerts", sa.Column("sla_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE security_alerts SET sla_notified_at = now() WHERE state = 'open'")


def downgrade() -> None:
    op.drop_column("security_alerts", "sla_notified_at")
