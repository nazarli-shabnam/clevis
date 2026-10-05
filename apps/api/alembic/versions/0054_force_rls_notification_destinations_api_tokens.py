"""FORCE row level security on notification_destinations and api_tokens.

0048 and 0049 enabled RLS and added a tenant_isolation policy but, unlike 0031/0046, not FORCE, so
the table owner was exempt. FORCE closes that.

``resolve_api_token`` is SECURITY DEFINER and reads ``api_tokens`` as the table owner *before* any
tenant context exists; it relied on the owner bypassing RLS. Under FORCE the owner is subject to the
tenant_isolation policy, so the lookup would match nothing and every machine token would 401. To keep
it working the function now sets ``app.api_token_lookup = 'on'`` for its own duration (a function-level
SET, restored on exit) and a SELECT-only ``token_lookup`` policy admits rows while that setting is on.
This is the same trust model as ``app.tenant_id``: the policies guard against application bugs
(a missing tenant filter), not against a caller who can already issue arbitrary SQL.

Same caveat as 0031/0046: a superuser DB_USER bypasses RLS regardless, so this is inert until the
operator opts into the non-superuser clevis_api role (see docs/self-hosting.md). No data is touched;
the function keeps its owner, ACL and signature (CREATE OR REPLACE).

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

_FORCE_TABLES = ["notification_destinations", "api_tokens"]

_RESOLVE_BODY = """
    SELECT t.id, t.tenant_id, o.id, o.github_login, t.scope
    FROM api_tokens t JOIN orgs o ON o.tenant_id = t.tenant_id
    WHERE t.token_hash = p_hash AND t.revoked_at IS NULL
    LIMIT 1
"""


def _resolve_api_token(extra_set: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION resolve_api_token(p_hash text)
        RETURNS TABLE (token_id integer, tenant_id integer, org_id integer, org_login text, scope text)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public{extra_set}
        AS $${_RESOLVE_BODY}$$;
    """


def upgrade() -> None:
    op.execute(sa.text("CREATE POLICY token_lookup ON api_tokens FOR SELECT USING (current_setting('app.api_token_lookup', true) = 'on')"))
    op.execute(_resolve_api_token("\n        SET app.api_token_lookup = 'on'"))
    for table in _FORCE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def downgrade() -> None:
    for table in _FORCE_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    op.execute(_resolve_api_token(""))
    op.execute(sa.text("DROP POLICY IF EXISTS token_lookup ON api_tokens"))
