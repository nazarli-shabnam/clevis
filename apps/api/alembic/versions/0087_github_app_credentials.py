"""Add github_app_credentials: the GitHub App's credentials when Clevis created the App itself.

A workspace admin can now create the GitHub App from Settings with GitHub's App manifest flow instead of
registering it by hand and copying five values into ``.env``. GitHub returns the App id, slug, client id and
secret, webhook secret and private key exactly once, so they are stored here, **encrypted** with the same Fernet
key as saved tokens (``JOB_SECRET_KEY``); only ``app_id``, ``slug`` and ``client_id`` are non-secret. Environment
variables (``GITHUB_APP_*``) keep working and take precedence field by field, so existing installs are unaffected.

Schema change: one new table, no existing data touched, nothing backfilled. At most one row (``id`` is pinned to 1
by a CHECK), because an instance has one App. Not tenant data: no RLS. The API role gets SELECT/INSERT/UPDATE/DELETE
on it; the worker role is deliberately granted nothing, so a compromised worker cannot read the App's private key.
Downgrade drops the table: an App created through the manifest flow then has to be configured through the
environment variables again (the credentials only exist here, so export them first).

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
    op.create_table(
        "github_app_credentials",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("app_id", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=True),
        sa.Column("client_id", sa.Text(), nullable=True),
        sa.Column("client_secret_enc", sa.Text(), nullable=True),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
        sa.Column("private_key_enc", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_github_app_credentials_single_row"),
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON github_app_credentials TO clevis_api;
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
                REVOKE SELECT, INSERT, UPDATE, DELETE ON github_app_credentials FROM clevis_api;
            END IF;
        END
        $$;
        """
    )
    op.drop_table("github_app_credentials")
