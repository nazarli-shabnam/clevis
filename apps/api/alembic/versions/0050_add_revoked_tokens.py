"""Add revoked_tokens: a per-session denylist so logout revokes the presented JWT.

``POST /auth/logout`` used to only clear the cookie, so a copied bearer token stayed valid until
it expired (30 days). Session JWTs now carry a ``jti`` claim, and logout records that ``jti`` here
until the token's own ``exp``; ``require_auth`` rejects a token whose ``jti`` is listed.
``token_version`` stays the "log out everywhere" counter.

- New table only: no existing data is read, rewritten or dropped, so there is no backfill and no
  data-loss risk. Tokens issued before this change have no ``jti`` and keep working until they
  expire (or ``/me/revoke-sessions``), exactly as before.
- Not tenant data, so no RLS: ``require_auth`` has to read it before any tenant context exists.
- ``expires_at`` is indexed so the opportunistic purge of expired rows stays cheap.
- Grants ``clevis_api`` DML when that role exists (same pattern as 0048/0049).

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_revoked_tokens_expires_at", "revoked_tokens", ["expires_at"])
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_api') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON revoked_tokens TO clevis_api;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_revoked_tokens_expires_at", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
