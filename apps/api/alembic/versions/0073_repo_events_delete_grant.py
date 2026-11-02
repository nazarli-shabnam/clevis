"""Let clevis_api DELETE from repo_events, for the retention sweep.

Migration 0036 granted clevis_api only SELECT and INSERT on repo_events, so under the restricted
runtime role the retention sweep's purge fails with "permission denied" and old events are never
removed. Guarded on the role existing, like the other grants: a deployment still running as the
superuser (no clevis_api) is unaffected.

Revision ID: 0073
Revises: 0072
Create Date: 2026-11-02
"""

from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT DELETE ON repo_events TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                REVOKE DELETE ON repo_events FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
