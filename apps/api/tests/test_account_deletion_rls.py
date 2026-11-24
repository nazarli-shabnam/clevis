"""The only-admin check of account deletion must see every admin of the org under the clevis_api role.

`memberships` is `tenant_id = app.tenant_id OR user_id = app.user_id` under FORCE RLS, and `require_auth` sets
only `app.user_id`, so without the org's tenant context the count sees just the caller's own row (and locks
only that row). Uses real connections with real commits, not the savepoint `db` fixture. Skips unless running
as clevis_api or API_DB_PASSWORD is set.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import User, set_session_user
from src.core.rbac import set_tenant_session_context
from src.repositories import org_membership_repo, org_repo, tenant_repo
from src.services import account_deletion


@pytest.fixture
def two_admin_org(clevis_api_engine):
    """A clevis_api session plus a committed org with admins A and B, all cleaned up afterwards."""
    with clevis_api_engine.connect() as conn:
        session = Session(bind=conn, expire_on_commit=False)
        a = User(email="rls-del-a@test.local", name=None, password_hash=None, is_workspace_admin=False)
        b = User(email="rls-del-b@test.local", name=None, password_hash=None, is_workspace_admin=False)
        session.add_all([a, b])
        session.commit()
        org = org_repo.ensure_tenant_linked(session, org_repo.get_or_create(session, github_login="rls-del-org"))
        tenant = tenant_repo.get_or_create_org_tenant(session, org.id)
        set_tenant_session_context(session, tenant.id, a.id)
        for user in (a, b):
            org_membership_repo.get_or_create(session, org_id=org.id, user_id=user.id, role="admin", source="invite")
        try:
            yield session, a, b, org, tenant
        finally:
            session.rollback()
            set_tenant_session_context(session, tenant.id, a.id)
            session.execute(text("DELETE FROM memberships WHERE tenant_id = :t"), {"t": tenant.id})
            session.execute(text("UPDATE orgs SET tenant_id = NULL WHERE id = :o"), {"o": org.id})
            session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant.id})
            session.execute(text("DELETE FROM orgs WHERE id = :o"), {"o": org.id})
            session.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": a.id, "b": b.id})
            session.execute(text("RESET app.tenant_id"))
            session.execute(text("RESET app.user_id"))
            session.commit()
            session.close()


def _as_require_auth(session: Session, user: User) -> None:
    # What the delete route has when it calls blockers(): app.user_id only, no tenant.
    session.commit()
    session.execute(text("RESET app.tenant_id"))
    session.info.pop("tenant_id", None)
    set_session_user(session, user.id)


def test_an_admin_of_an_org_with_another_admin_is_not_blocked(two_admin_org):
    session, a, _b, _org, _tenant = two_admin_org
    _as_require_auth(session, a)

    assert account_deletion.blockers(session, a) == []


def test_the_only_admin_is_still_blocked(two_admin_org):
    session, a, b, org, tenant = two_admin_org
    set_tenant_session_context(session, tenant.id, a.id)
    org_membership_repo.delete(session, org_id=org.id, user_id=b.id)
    _as_require_auth(session, a)

    assert account_deletion.blockers(session, a) == [
        "You are the only admin of rls-del-org; make someone else an admin first"
    ]
