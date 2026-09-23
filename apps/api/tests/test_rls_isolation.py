"""End-to-end proof that RLS blocks cross-tenant access under the non-superuser clevis_api role.

Doesn't use the `db` fixture: it usually runs as the superuser, which bypasses RLS. Connects as
clevis_api (CI) or via API_DB_PASSWORD, else skips. Uses audit_logs for its plain equality policy.
"""

import os
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import AuditLog, Tenant, User
from src.core.rbac import set_tenant_session_context


def _clevis_api_engine():
    url = urlsplit(settings.database_url.get_secret_value())
    if url.username == "clevis_api":
        return create_engine(settings.database_url.get_secret_value())

    password = os.environ.get("API_DB_PASSWORD")
    if not password:
        pytest.skip(
            "neither DATABASE_URL nor API_DB_PASSWORD point at clevis_api -- "
            "provision the role (docker/provision-api-role-existing-deployment.sh) "
            "and set API_DB_PASSWORD to exercise this test locally"
        )
    port = f":{url.port}" if url.port is not None else ""
    netloc = f"clevis_api:{quote(password, safe='')}@{url.hostname}{port}"
    return create_engine(urlunsplit((url.scheme, netloc, url.path, url.query, url.fragment)))


def test_rls_blocks_cross_tenant_audit_log_access():
    engine = _clevis_api_engine()
    with engine.connect() as conn:
        # expire_on_commit=False: a refresh under tenant B's context would raise ObjectDeletedError.
        session = Session(bind=conn, expire_on_commit=False)
        # Initialized up front so a partial setup failure still cleans up whatever committed.
        user_a = user_b = tenant_a = tenant_b = log_a = log_b = None
        try:
            try:
                user_a = User(email="rls-isolation-a@test.local", name=None, password_hash=None, is_workspace_admin=False)
                user_b = User(email="rls-isolation-b@test.local", name=None, password_hash=None, is_workspace_admin=False)
                session.add_all([user_a, user_b])
                session.commit()

                tenant_a = Tenant(kind="personal", personal_user_id=user_a.id)
                tenant_b = Tenant(kind="personal", personal_user_id=user_b.id)
                session.add_all([tenant_a, tenant_b])
                session.commit()

                # Insert under each tenant's own context: with no WITH CHECK, the USING clause gates inserts.
                set_tenant_session_context(session, tenant_a.id, user_a.id)
                log_a = AuditLog(actor="tester", action="test", target="a", payload="{}", tenant_id=tenant_a.id)
                session.add(log_a)
                session.commit()

                set_tenant_session_context(session, tenant_b.id, user_b.id)
                log_b = AuditLog(actor="tester", action="test", target="b", payload="{}", tenant_id=tenant_b.id)
                session.add(log_b)
                session.commit()

                # Back in tenant A's context: tenant B's row must be invisible to SELECT,
                # and untouched by UPDATE/DELETE targeting it directly by id.
                set_tenant_session_context(session, tenant_a.id, user_a.id)
                visible_ids = {row[0] for row in session.execute(text("SELECT id FROM audit_logs")).all()}
                assert visible_ids == {log_a.id}

                update_result = session.execute(
                    text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"), {"id": log_b.id}
                )
                assert update_result.rowcount == 0
                session.commit()

                delete_result = session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": log_b.id})
                assert delete_result.rowcount == 0
                session.commit()

                # And the reverse, confirming isolation isn't just a one-directional fluke.
                set_tenant_session_context(session, tenant_b.id, user_b.id)
                visible_ids = {row[0] for row in session.execute(text("SELECT id FROM audit_logs")).all()}
                assert visible_ids == {log_b.id}
            finally:
                # RLS applies to DELETE too, so clean up per tenant; roll back first in case of a prior error.
                session.rollback()
                if log_a is not None:
                    set_tenant_session_context(session, tenant_a.id, user_a.id)
                    session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": log_a.id})
                    session.commit()
                if log_b is not None:
                    set_tenant_session_context(session, tenant_b.id, user_b.id)
                    session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": log_b.id})
                    session.commit()
                session.execute(text("RESET app.tenant_id"))
                session.execute(text("RESET app.user_id"))
                if tenant_a is not None:
                    session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_a.id})
                if tenant_b is not None:
                    session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_b.id})
                if user_a is not None:
                    session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_a.id})
                if user_b is not None:
                    session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_b.id})
                session.commit()
        finally:
            session.close()
    engine.dispose()


# Every table with FORCE RLS. Plain pg_catalog metadata, so this check never skips.
_EXPECTED_FORCE_RLS_TABLES = {
    "memberships",
    "github_installations",
    "scan_results",
    "repo_events",
    "repo_event_daily_counts",
    "activity_sync_cursors",
    "security_alerts",
    "org_members",
    "repo_collaborators",
    "org_membership_sync_cursors",
    "automation_repo_settings",
}


def test_every_tenant_table_has_force_rls(db):
    rows = db.execute(
        text(
            "SELECT relname FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relforcerowsecurity"
        )
    ).fetchall()
    assert {row[0] for row in rows} == _EXPECTED_FORCE_RLS_TABLES
