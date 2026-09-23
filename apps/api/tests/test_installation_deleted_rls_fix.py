"""installation.deleted webhook must delete under the non-superuser clevis_api role with no RLS session context.

Uses a real connection with real commits (not the savepoint `db` fixture) so no session
context leaks in. Skips unless running as clevis_api or API_DB_PASSWORD is set.
"""

import os
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import GitHubInstallation, Org, Tenant, User, set_session_user
from src.core.rbac import set_tenant_session_context
from src.repositories import installation_repo, org_repo


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


def test_installation_deleted_removes_the_row_with_no_session_context_set():
    engine = _clevis_api_engine()
    with engine.connect() as conn:
        session = Session(bind=conn, expire_on_commit=False)
        user = org = tenant = installation = None
        try:
            try:
                # Mirrors sync_org_installation: resolve the tenant and set session context before writing.
                user = User(
                    email="installation-rls-fix@test.local", name=None, password_hash=None, is_workspace_admin=False
                )
                session.add(user)
                session.commit()

                org = Org(github_login="installation-rls-fix-org")
                session.add(org)
                session.commit()

                # Not a direct org.tenant_id assignment: orgs' WITH CHECK policy requires app.tenant_id
                # to match on UPDATE, and ensure_tenant_linked is the one path allowed to link a new tenant.
                org = org_repo.ensure_tenant_linked(session, org)
                tenant = session.query(Tenant).filter(Tenant.id == org.tenant_id).first()

                set_tenant_session_context(session, tenant.id, user.id)
                installation = installation_repo.create(
                    session,
                    account_login="installation-rls-fix-org",
                    account_type="Organization",
                    auth_mode="app",
                    installation_id=987654,
                    org_id=org.id,
                )
                assert installation.tenant_id == tenant.id

                # Model the unauthenticated webhook request: with no session context RLS's equality
                # policy evaluates to NULL and would silently match zero rows.
                session.execute(text("RESET app.tenant_id"))
                session.execute(text("RESET app.user_id"))

                removed, resolved_tenant_id = installation_repo.delete_by_installation_id(session, 987654)

                assert removed == 1
                assert resolved_tenant_id == tenant.id

                # Re-resolve via the RLS-bypassing SECURITY DEFINER lookup so this check isn't fooled by RLS.
                still_present = session.execute(
                    text("SELECT resolve_installation_tenant_id(:installation_id)"), {"installation_id": 987654}
                ).scalar()
                assert still_present is None
            finally:
                session.rollback()
                # Restore session context so cleanup writes aren't blocked by RLS (user-only if
                # tenant resolution failed).
                if user is not None:
                    if tenant is not None:
                        set_tenant_session_context(session, tenant.id, user.id)
                    else:
                        set_session_user(session, user.id)
                if installation is not None:
                    session.execute(
                        text("DELETE FROM github_installations WHERE installation_id = :iid"), {"iid": 987654}
                    )
                if org is not None:
                    session.execute(text("UPDATE orgs SET tenant_id = NULL WHERE id = :id"), {"id": org.id})
                if tenant is not None:
                    session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant.id})
                if org is not None:
                    session.execute(text("DELETE FROM orgs WHERE id = :id"), {"id": org.id})
                if user is not None:
                    session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
                session.execute(text("RESET app.tenant_id"))
                session.execute(text("RESET app.user_id"))
                session.commit()
        finally:
            session.close()
    engine.dispose()


def test_resolve_installation_tenant_id_bypasses_rls_with_no_session_context():
    """The SECURITY DEFINER lookup resolves tenant_id from installation_id regardless of session context."""
    engine = _clevis_api_engine()
    with engine.connect() as conn:
        session = Session(bind=conn, expire_on_commit=False)
        user = org = tenant = None
        try:
            try:
                user = User(
                    email="resolve-fn-rls-fix@test.local", name=None, password_hash=None, is_workspace_admin=False
                )
                session.add(user)
                session.commit()

                org = Org(github_login="resolve-fn-rls-fix-org")
                session.add(org)
                session.commit()

                org = org_repo.ensure_tenant_linked(session, org)
                tenant = session.query(Tenant).filter(Tenant.id == org.tenant_id).first()

                set_tenant_session_context(session, tenant.id, user.id)
                installation_repo.create(
                    session,
                    account_login="resolve-fn-rls-fix-org",
                    account_type="Organization",
                    auth_mode="app",
                    installation_id=987655,
                    org_id=org.id,
                )
                session.commit()

                session.execute(text("RESET app.tenant_id"))
                session.execute(text("RESET app.user_id"))

                # Sanity check: with no session context, a plain SELECT is blocked by RLS.
                blocked = session.execute(
                    text("SELECT id FROM github_installations WHERE installation_id = :iid"), {"iid": 987655}
                ).fetchall()
                assert blocked == []

                resolved = session.execute(
                    text("SELECT resolve_installation_tenant_id(:iid)"), {"iid": 987655}
                ).scalar()
                assert resolved == tenant.id
            finally:
                session.rollback()
                if user is not None:
                    if tenant is not None:
                        set_tenant_session_context(session, tenant.id, user.id)
                    else:
                        set_session_user(session, user.id)
                session.execute(
                    text("DELETE FROM github_installations WHERE installation_id = :iid"), {"iid": 987655}
                )
                if org is not None:
                    session.execute(text("UPDATE orgs SET tenant_id = NULL WHERE id = :id"), {"id": org.id})
                if tenant is not None:
                    session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant.id})
                if org is not None:
                    session.execute(text("DELETE FROM orgs WHERE id = :id"), {"id": org.id})
                if user is not None:
                    session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
                session.execute(text("RESET app.tenant_id"))
                session.execute(text("RESET app.user_id"))
                session.commit()
        finally:
            session.close()
    engine.dispose()
