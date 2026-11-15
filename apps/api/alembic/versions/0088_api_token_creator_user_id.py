"""Track which user created an API token and let an org auto-revoke a token when its creator leaves.

``api_tokens.created_by`` is only an email string, so a token kept working after its creator was removed from
the org and nothing could tell. This adds:

- ``api_tokens.created_by_user_id`` (nullable FK to users, ON DELETE SET NULL). Backfilled once from the existing
  ``created_by`` email with a case-insensitive exact match to ``users.email``; a token whose email no longer matches
  any user stays NULL (unknown creator: never flagged, never auto-revoked).
- ``orgs.revoke_tokens_on_creator_leave`` (NOT NULL, default false): when true, removing a member revokes the
  active tokens they created in that org. Off by default, so no existing org changes behaviour.
- Narrow grants for ``clevis_worker`` (only when that role exists): the worker's membership-removal handlers
  (``member_removed`` and the reconcile job) revoke those tokens in the same transaction, so it needs to read the
  four ``api_tokens`` columns it filters on, update ``revoked_at`` only, and read the org flag.

Schema change: two additive columns plus the one-time backfill; no existing value is rewritten or dropped. The
backfill is best-effort (an email reassigned to a different user would attribute the token to that user); it only
matters if that user is later removed while the org setting is on. Downgrade drops both columns and revokes the grants.

Revision ID: 0088
Revises: 0087
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None

_GRANTS = (
    "SELECT (id, tenant_id, name, created_by_user_id, revoked_at) ON api_tokens",
    "UPDATE (revoked_at) ON api_tokens",
    "SELECT (tenant_id, revoke_tokens_on_creator_leave) ON orgs",
)


def _for_worker(verb: str, preposition: str) -> None:
    for grant in _GRANTS:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'clevis_worker') THEN
                    {verb} {grant} {preposition} clevis_worker;
                END IF;
            END
            $$;
            """
        )


def upgrade() -> None:
    op.add_column(
        "api_tokens",
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "orgs",
        sa.Column("revoke_tokens_on_creator_leave", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute(
        sa.text(
            "UPDATE api_tokens t SET created_by_user_id = u.id FROM users u "
            "WHERE t.created_by_user_id IS NULL AND lower(u.email) = lower(t.created_by)"
        )
    )
    _for_worker("GRANT", "TO")


def downgrade() -> None:
    _for_worker("REVOKE", "FROM")
    op.drop_column("orgs", "revoke_tokens_on_creator_leave")
    op.drop_column("api_tokens", "created_by_user_id")
