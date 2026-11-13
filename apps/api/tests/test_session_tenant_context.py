"""set_session_tenant must survive commits: a commit returns the connection to the pool and the next
statement may draw a different one (issue #957)."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import set_session_tenant

_SHOW = text("select current_setting('app.tenant_id', true)")


def test_tenant_context_survives_commits_on_a_warm_pool():
    engine = create_engine(settings.database_url.get_secret_value(), pool_size=10, max_overflow=10)
    try:
        warm = [engine.connect() for _ in range(4)]  # other work has used the pool
        for c in warm:
            c.close()
        with Session(engine) as db:
            set_session_tenant(db, 7)
            for _ in range(5):
                assert db.execute(_SHOW).scalar() == "7"
                db.commit()
            db.rollback()
            assert db.execute(_SHOW).scalar() == "7"
            set_session_tenant(db, 9)
            db.commit()
            assert db.execute(_SHOW).scalar() == "9"
        # Transaction-scoped: nothing is left on any pooled connection.
        with engine.connect() as conn:
            assert conn.execute(_SHOW).scalar() in (None, "")
    finally:
        engine.dispose()
