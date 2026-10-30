"""The uninstall purge must really delete under the restricted clevis_api role.

clevis_api has no DELETE on five of the purged tables, so the purge runs through the SECURITY DEFINER
purge_tenant_github_data() (migration 0069). Uses real connections with real commits. Skips unless running as
clevis_api or API_DB_PASSWORD is set.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.core.db import User
from src.core.rbac import set_tenant_session_context
from src.repositories import installation_repo, org_repo, tenant_repo

_INSERTS = (
    "INSERT INTO repo_events (tenant_id, delivery_id, event_type, actor, actor_avatar, repo, summary, occurred_at)"
    " VALUES (:t, 'd1', 'push', 'a', '', 'r', 's', now())",
    "INSERT INTO repo_event_daily_counts (tenant_id, repo, event_type, day) VALUES (:t, 'r', 'push', current_date)",
    "INSERT INTO activity_sync_cursors (tenant_id, account_login, account_type) VALUES (:t, 'acme', 'Organization')",
    "INSERT INTO security_alerts (tenant_id, repo, kind, number, state, created_at, updated_at)"
    " VALUES (:t, 'r', 'dependabot', 1, 'open', now(), now())",
    "INSERT INTO org_membership_sync_cursors (tenant_id, org_login) VALUES (:t, 'acme')",
)
_TABLES = ("repo_events", "repo_event_daily_counts", "activity_sync_cursors", "security_alerts", "org_membership_sync_cursors")


@pytest.fixture
def seeded_tenant(clevis_api_engine):
    with clevis_api_engine.connect() as conn:
        session = Session(bind=conn, expire_on_commit=False)
        user = User(email="purge-rls@test.local", name=None, password_hash=None, is_workspace_admin=False)
        session.add(user)
        session.commit()
        org = org_repo.ensure_tenant_linked(session, org_repo.get_or_create(session, github_login="purge-rls-org"))
        tenant = tenant_repo.get_or_create_org_tenant(session, org.id)
        set_tenant_session_context(session, tenant.id, user.id)
        for sql in _INSERTS:
            session.execute(text(sql), {"t": tenant.id})
        session.commit()
        try:
            yield session, tenant.id
        finally:
            session.rollback()
            set_tenant_session_context(session, tenant.id, user.id)
            session.execute(text("DELETE FROM github_installations WHERE tenant_id = :t"), {"t": tenant.id})
            # clevis_api cannot delete these tables directly, so clean up through the purge itself.
            session.execute(text("SELECT purge_tenant_github_data(:t)"), {"t": tenant.id})
            session.execute(text("UPDATE orgs SET tenant_id = NULL WHERE id = :o"), {"o": org.id})
            session.execute(text("DELETE FROM memberships WHERE tenant_id = :t"), {"t": tenant.id})
            session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant.id})
            session.execute(text("DELETE FROM orgs WHERE id = :o"), {"o": org.id})
            session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
            session.execute(text("RESET app.tenant_id"))
            session.execute(text("RESET app.user_id"))
            session.commit()
            session.close()


def _counts(session: Session, tenant_id: int) -> dict[str, int]:
    return {
        t: session.execute(text(f"SELECT count(*) FROM {t} WHERE tenant_id = :t"), {"t": tenant_id}).scalar()
        for t in _TABLES
    }


def test_purge_removes_rows_from_tables_clevis_api_cannot_delete(seeded_tenant):
    session, tenant_id = seeded_tenant
    assert all(n == 1 for n in _counts(session, tenant_id).values())

    installation_repo.purge_tenant_github_data_if_disconnected(session, tenant_id)

    assert _counts(session, tenant_id) == dict.fromkeys(_TABLES, 0)


def test_purge_function_refuses_a_tenant_other_than_the_session_tenant(seeded_tenant):
    session, tenant_id = seeded_tenant

    with pytest.raises(DBAPIError, match="session tenant does not match"):
        session.execute(text("SELECT purge_tenant_github_data(:t)"), {"t": tenant_id + 1_000_000})
    session.rollback()

    assert all(n == 1 for n in _counts(session, tenant_id).values())
