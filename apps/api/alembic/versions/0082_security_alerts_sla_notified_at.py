"""Add security_alerts.sla_notified_at: when a Dependabot alert's SLA breach was announced.

The API's SLA-breach sweep stamps this before messaging ``sla_breach`` alert destinations, so each breach
is announced at most once (a restart or a second replica never repeats it). Like ``alert_notified_at`` the
stamp means "processed", not "delivered".

Schema change: one nullable column, no index (the sweep filters on the already-indexed tenant and state).
Open alerts that are already past their SLA are stamped at upgrade time (using the configured windows, else the
7/30-day defaults), so rolling this out does not announce every such alert in one burst (the in-app feed and the
burn-down page still show them). Alerts still inside their window stay unstamped, so their breach is announced
when it happens. Downgrade drops the column.

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
    conn = op.get_bind()
    windows = {}
    for severity, default in (("critical", 7), ("high", 30)):
        raw = conn.execute(
            sa.text("SELECT value FROM app_config WHERE key = :k"), {"k": f"dependabot_sla_{severity}_days"}
        ).scalar()
        try:
            days = int(raw) if raw is not None else default
        except ValueError:
            days = default
        windows[severity] = days if days >= 1 else default
    conn.execute(
        sa.text(
            "UPDATE security_alerts SET sla_notified_at = now() "
            "WHERE state = 'open' AND kind = 'dependabot' AND ("
            "(severity = 'critical' AND created_at <= now() - make_interval(days => :crit)) OR "
            "(severity = 'high' AND created_at <= now() - make_interval(days => :high)))"
        ),
        {"crit": windows["critical"], "high": windows["high"]},
    )


def downgrade() -> None:
    op.drop_column("security_alerts", "sla_notified_at")
