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
    "notification_destinations",
    "notification_reads",
    "api_tokens",
}


def test_every_tenant_table_has_force_rls(db):
    rows = db.execute(
        text(
            "SELECT relname FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relforcerowsecurity"
        )
    ).fetchall()
    assert {row[0] for row in rows} == _EXPECTED_FORCE_RLS_TABLES


def test_resolve_api_token_still_works_when_the_owner_is_subject_to_rls(db):
    """FORCE RLS applies the policies to the table owner, which is who the SECURITY DEFINER lookup runs as.

    Hands api_tokens and the function to a fresh non-superuser role inside the test transaction (rolled
    back afterwards), then resolves a token with no tenant context. Needs a superuser connection."""
    if not db.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar():
        pytest.skip("needs a superuser connection to create a throwaway owner role")
    from src.repositories import api_token_repo, org_repo

    org = org_repo.get_or_create(db, github_login="force-rls-org")
    _, token = api_token_repo.create(db, tenant_id=org.tenant_id, name="ci", created_by="t@e.com")
    db.flush()
    db.execute(text("CREATE ROLE rls_probe_owner NOSUPERUSER NOBYPASSRLS"))
    db.execute(text("GRANT USAGE ON SCHEMA public TO rls_probe_owner"))
    db.execute(text("GRANT SELECT ON orgs TO rls_probe_owner"))
    db.execute(text("ALTER TABLE api_tokens OWNER TO rls_probe_owner"))
    db.execute(text("ALTER FUNCTION resolve_api_token(text) OWNER TO rls_probe_owner"))
    db.execute(text("RESET app.tenant_id"))
    db.execute(text("SET LOCAL ROLE rls_probe_owner"))
    try:
        # The lookup policy admits the table owner by identity (migration 0056), so a read by the owner
        # is allowed and the definer function, which runs as the owner, resolves the token. Non-owner
        # roles stay locked out (see test_api_tokens.py).
        row = db.execute(
            text("SELECT org_login FROM resolve_api_token(:h)"), {"h": api_token_repo.hash_token(token)}
        ).first()
        assert row is not None and row[0] == "force-rls-org"
    finally:
        db.execute(text("RESET ROLE"))
