"""grant clevis_worker role privileges on jobs and app_config

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-31
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                GRANT SELECT, UPDATE ON jobs TO clevis_worker;
                GRANT SELECT ON app_config TO clevis_worker;
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
                REVOKE SELECT, UPDATE ON jobs FROM clevis_worker;
                REVOKE SELECT ON app_config FROM clevis_worker;
            END IF;
        END
        $$;
        """
    )
