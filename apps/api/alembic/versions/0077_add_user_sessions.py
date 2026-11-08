"""Add user_sessions: a record of issued sessions so a user can see them and revoke one.

Profile only offered "Sign out of all devices" (bump ``users.token_version``), which also logs out
the device the user is on, and there was no way to see which sessions exist. Every session JWT
already carries a unique ``jti``; this table records one row per issued session (jti, owner, when it
was created and last seen, when it expires, plus a user-agent and IP hint) so the Profile page can list
them. Revoking one session writes its ``jti`` to the existing ``revoked_tokens`` denylist and deletes
its row here, so ``require_auth`` needs no new check for revocation.

- New table only: nothing existing is read, rewritten or dropped, and nothing is backfilled. Sessions
  issued before this migration (and legacy tokens with no ``jti``) simply do not appear in the list; they
  keep working and can still be ended with "Sign out of all devices".
- Not tenant data, so no RLS: like ``revoked_tokens``, ``require_auth`` touches it before any tenant
  context exists. Every query is filtered by the authenticated user's id.
- Holds personal data (the IP and user agent a session was started from). The IP is ``request.client.host``,
  the same value the login rate limiter uses, so behind a proxy that does not forward the client address
  it is the proxy's. Rows are removed on logout, on revoke, on "Sign out of all devices", when the
  session expires (purged on that user's next sign-in) and with the user (``ON DELETE CASCADE``).
- ``user_id`` is indexed for the per-user list and purge.
- Grants ``clevis_api`` DML when that role exists (same pattern as 0050).

Revision ID: 0077
Revises: 0076
"""

import sqlalchemy as sa
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("jti", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON user_sessions TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
