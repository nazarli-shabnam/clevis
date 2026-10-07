"""Whether Postgres Row-Level Security actually applies to this process's database role.

The RLS policies on the tenant tables only bind a role that is neither a superuser nor `BYPASSRLS`.
In the default deployment (no `API_DB_PASSWORD`) the API connects as the `initdb` bootstrap superuser,
so every policy is silently inert and tenant isolation is application-layer only. That is a deliberate
opt-in default (see docs/self-hosting.md), but operators who don't know to opt in should at least be told,
so the app logs it once at startup.
"""

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Besides rolsuper / rolbypassrls, a role that OWNS the tenant tables is exempt from policies on the tables that
# only have ENABLE (not FORCE) -- e.g. orgs -- so it counts too (the migrations run as DB_USER, which owns them).
_ROLE_QUERY = text(
    "SELECT current_user, r.rolsuper, r.rolbypassrls OR EXISTS ("
    "SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = 'orgs' AND tableowner = current_user"
    ") FROM pg_roles r WHERE r.rolname = current_user"
)

WARNING = (
    "Row-Level Security is NOT enforced: the database role %r is a superuser, has BYPASSRLS or owns the tables, so tenant "
    "isolation relies on application-level filters only. To enforce RLS as a second layer, give the API its "
    "own role by setting API_DB_PASSWORD (and the worker WORKER_DB_PASSWORD); see 'Enabling Row-Level Security "
    "enforcement' in docs/self-hosting.md."
)


def rls_bypassed(engine: Engine) -> tuple[str, bool] | None:
    """(role name, whether it bypasses RLS), or None if it could not be determined."""
    try:
        with engine.connect() as conn:
            row = conn.execute(_ROLE_QUERY).first()
    except Exception:
        logger.warning("could not determine whether Row-Level Security applies to the database role", exc_info=True)
        return None
    if row is None:
        return None
    role, is_super, bypass_rls = row
    return role, bool(is_super or bypass_rls)


def warn_if_rls_bypassed(engine: Engine) -> bool:
    """Log a warning when RLS is inert for this connection. Best effort: never raises, never blocks startup.
    Returns True when the warning was logged."""
    result = rls_bypassed(engine)
    if result is None or not result[1]:
        return False
    logger.warning(WARNING, result[0])
    return True
