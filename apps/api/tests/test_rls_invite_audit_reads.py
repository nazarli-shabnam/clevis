"""Cross-tenant reads that have no tenant to set must still work under the clevis_api role.

`invitations` and `audit_logs` carry a strict `tenant_id = app.tenant_id` policy. Login's pending-invite
lookup and the workspace-admin audit log are instance-wide, so they read through SECURITY DEFINER
functions (migration 0068). Uses real connections with real commits (not the savepoint `db` fixture, whose
outer transaction would leak an earlier SET LOCAL across the test). Skips unless running as clevis_api or
API_DB_PASSWORD is set.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import AuditLog, Invitation, User
from src.core.rbac import set_tenant_session_context
from src.repositories import audit_repo, invitation_repo, org_repo, tenant_repo
from src.routers import audit as audit_router


@pytest.fixture
def tenant_session(clevis_api_engine):
    """A clevis_api session plus a committed user/org/tenant, with all of it cleaned up afterwards."""
    with clevis_api_engine.connect() as conn:
        session = Session(bind=conn, expire_on_commit=False)
        user = User(email="rls-reads@test.local", name=None, password_hash=None, is_workspace_admin=True)
        session.add(user)
        session.commit()
        org = org_repo.ensure_tenant_linked(session, org_repo.get_or_create(session, github_login="rls-reads-org"))
        tenant = tenant_repo.get_or_create_org_tenant(session, org.id)
        set_tenant_session_context(session, tenant.id, user.id)
        try:
            yield session, user, org, tenant
        finally:
            session.rollback()
            set_tenant_session_context(session, tenant.id, user.id)
            session.execute(text("DELETE FROM invitations WHERE org_id = :o"), {"o": org.id})
            session.execute(text("DELETE FROM audit_logs WHERE tenant_id = :t"), {"t": tenant.id})
            session.execute(text("UPDATE orgs SET tenant_id = NULL WHERE id = :o"), {"o": org.id})
            session.execute(text("DELETE FROM memberships WHERE tenant_id = :t"), {"t": tenant.id})
            session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant.id})
            session.execute(text("DELETE FROM orgs WHERE id = :o"), {"o": org.id})
            session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user.id})
            session.execute(text("RESET app.tenant_id"))
            session.execute(text("RESET app.user_id"))
            session.commit()
            session.close()


def _clear_context(session: Session) -> None:
    # Models the login / workspace-admin request: no tenant context for RLS to match.
    session.execute(text("RESET app.tenant_id"))
    session.execute(text("RESET app.user_id"))


def test_pending_invitations_for_email_is_visible_with_no_tenant_context(tenant_session):
    session, user, org, _tenant = tenant_session
    invitation_repo.create(session, org_id=org.id, email="Invitee@Test.Local", invited_by_user_id=user.id)
    _clear_context(session)

    # Sanity: RLS really hides the row from a plain query, so the assertion below isn't vacuous.
    assert session.query(Invitation).filter(Invitation.org_id == org.id).count() == 0

    found = invitation_repo.list_pending_for_email(session, "invitee@test.local")
    assert [inv.org_id for inv in found] == [org.id]


def test_audit_log_reads_are_instance_wide_with_no_tenant_context(tenant_session):
    session, user, _org, tenant = tenant_session
    audit_repo.write(session, actor="a", action="rls.reads.test", target="t", payload={}, tenant_id=tenant.id)
    _clear_context(session)

    assert session.query(AuditLog).filter(AuditLog.action == "rls.reads.test").count() == 0

    rows = audit_router.list_audit_logs(action="rls.reads.test", limit=10, db=session, _user=None)
    assert [r.action for r in rows] == ["rls.reads.test"]
    assert "rls.reads.test" in audit_router.list_audit_actions(db=session, _user=None)
