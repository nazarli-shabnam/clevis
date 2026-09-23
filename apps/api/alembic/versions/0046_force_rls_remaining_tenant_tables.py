"""Add FORCE ROW LEVEL SECURITY to the remaining tenant tables.

Unlike 0031, no self-access carve-out is needed first: these tables are only ever written
by the worker or by request handlers after an explicit tenant_id session var is set, so a
plain tenant_id-equality policy already covers every write.

CAVEAT: same as 0031 -- in the default docker-compose deployment DB_USER is a Postgres
superuser and bypasses RLS regardless of ENABLE/FORCE, so this is correct but inert until
an operator opts into the non-superuser clevis_api/clevis_worker roles (see
docs/self-hosting.md).

Revision ID: 0046
Revises: 0045
Create Date: 2026-09-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None

_FORCE_TABLES = [
    "repo_events",
    "repo_event_daily_counts",
    "activity_sync_cursors",
    "security_alerts",
    "org_members",
    "repo_collaborators",
    "org_membership_sync_cursors",
    "automation_repo_settings",
]


def upgrade() -> None:
    for table in _FORCE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def downgrade() -> None:
    for table in _FORCE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
