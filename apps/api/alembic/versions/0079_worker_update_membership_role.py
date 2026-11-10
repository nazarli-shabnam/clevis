"""Let clevis_worker demote a GitHub-sourced membership during membership reconciliation.

The reconcile job now applies a missed GitHub role change (admin to member) to ``memberships.role``
as well as revoking removed members. 0047 gave the worker ``SELECT, DELETE`` on ``memberships``; this adds
``UPDATE`` on the ``role`` column only, so the role split stays narrow (the worker cannot rewrite
``source``, ``user_id`` or ``tenant_id``).

Privilege change only: no data or schema change. Guarded on the role existing, so a deployment without
``WORKER_DB_PASSWORD`` (no ``clevis_worker``) is unaffected. Downgrade revokes the column grant again.

Revision ID: 0079
Revises: 0078
Create Date: 2026-11-10
"""

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT UPDATE (role) ON memberships TO clevis_worker;
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
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                REVOKE UPDATE (role) ON memberships FROM clevis_worker;
            END IF;
        END
        $$;
        """
    )
