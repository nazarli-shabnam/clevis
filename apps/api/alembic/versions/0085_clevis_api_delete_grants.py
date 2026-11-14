"""Let clevis_api DELETE from the tenant tables it could only read and write.

Account deletion removes every row stored under the user's personal tenant, table by table. Migrations 0036-0041,
0039 and 0080 granted clevis_api only SELECT/INSERT/UPDATE on these five tables, so under the restricted runtime
role the delete fails with "permission denied" (as 0073 did for repo_events). Grants only, no data is touched.
Guarded on the role existing, like the other grants: a deployment still running as the superuser is unaffected.

Revision ID: 0085
Revises: 0084
Create Date: 2026-11-14
"""

from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None

_TABLES = (
    "activity_sync_cursors",
    "org_membership_sync_cursors",
    "repo_event_daily_counts",
    "security_alert_syncs",
    "security_alerts",
)


def _statement(verb: str, preposition: str) -> str:
    stmts = "\n".join(f"{verb} DELETE ON {t} {preposition} clevis_api;" for t in _TABLES)
    return f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                {stmts}
            END IF;
        END
        $$;
        """


def upgrade() -> None:
    op.execute(_statement("GRANT", "TO"))


def downgrade() -> None:
    op.execute(_statement("REVOKE", "FROM"))
