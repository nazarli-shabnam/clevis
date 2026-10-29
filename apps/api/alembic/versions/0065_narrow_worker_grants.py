"""Narrow clevis_worker's table access to what the worker actually uses.

* ``users``: 0047 granted whole-table SELECT, but the worker's only query is
  ``SELECT id FROM users WHERE github_user_id = ...`` (``org_membership_store.revoke_github_membership``).
  A whole-table grant also exposes ``password_hash``, ``email_verify_token`` and other secrets, so it is
  replaced by a column-level ``SELECT (id, github_user_id)``.
* ``automation_repo_settings``: 0043 granted full CRUD, but nothing under ``apps/worker`` references the
  table (only the API reads and writes it), so the grant is dropped.

Privilege changes only: no data, schema or API-role change. A column-level grant does not stack on a
table-level one, so the table-level SELECT on ``users`` is revoked first. The statements are guarded on the
role existing, so a deployment without ``WORKER_DB_PASSWORD`` (no ``clevis_worker``) is unaffected.
Downgrade restores the two earlier grants exactly.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-29
"""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                REVOKE SELECT ON users FROM clevis_worker;
                GRANT SELECT (id, github_user_id) ON users TO clevis_worker;
                REVOKE SELECT, INSERT, UPDATE, DELETE ON automation_repo_settings FROM clevis_worker;
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
                REVOKE SELECT (id, github_user_id) ON users FROM clevis_worker;
                GRANT SELECT ON users TO clevis_worker;
                GRANT SELECT, INSERT, UPDATE, DELETE ON automation_repo_settings TO clevis_worker;
            END IF;
        END
        $$;
        """
    )
