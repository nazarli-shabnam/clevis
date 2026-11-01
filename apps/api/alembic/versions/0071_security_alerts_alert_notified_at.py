"""Add security_alerts.alert_notified_at: when a new critical/high alert was announced to alert destinations.

The alert sweep announces each alert once and stamps this column. Existing rows are stamped now so that
turning the event on does not replay history; only alerts ingested after this migration are announced.
Additive and nullable: no data is changed other than that stamp, and the worker's upsert never touches it.

Revision ID: 0071
Revises: 0070
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("security_alerts", sa.Column("alert_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE security_alerts SET alert_notified_at = now()"))


def downgrade() -> None:
    op.drop_column("security_alerts", "alert_notified_at")
