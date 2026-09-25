"""Tests for installation_repo.upsert, focused on the concurrent-sync race path."""

from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

from src.repositories import installation_repo, org_repo


def _acme_org_id(db) -> int:
    # github_installations.org_id has a foreign key to orgs.id -- needs a real row.
    return org_repo.get_or_create(db, github_login="acme").id


def test_upsert_creates_new_row(db):
    org_id = _acme_org_id(db)
    row = installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=1, org_id=org_id
    )
    assert row.account_login == "acme"
    assert row.installation_id == 1


def test_upsert_sets_tenant_id_for_org_scoped_installation(db):
    from src.core.db import Tenant

    org_id = _acme_org_id(db)
    row = installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=1, org_id=org_id
    )
    assert row.tenant_id is not None
    tenant = db.query(Tenant).filter(Tenant.id == row.tenant_id).first()
    assert tenant.kind == "org"
    assert tenant.org_id == org_id


def test_upsert_sets_tenant_id_for_personal_installation(db):
    from src.core.db import Tenant, User

    user = User(email="dev2@example.com", name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    row = installation_repo.upsert(
        db, account_login="octocat", account_type="User", auth_mode="app", installation_id=7, owner_user_id=user.id
    )

    assert row.tenant_id is not None
    tenant = db.query(Tenant).filter(Tenant.id == row.tenant_id).first()
    assert tenant.kind == "personal"
    assert tenant.personal_user_id == user.id


def test_upsert_update_branch_backfills_tenant_id(db):
    org_id = _acme_org_id(db)
    installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=1, org_id=org_id
    )
    updated = installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=2, org_id=org_id
    )
    assert updated.tenant_id is not None


def test_upsert_updates_existing_row(db):
    org_id = _acme_org_id(db)
    installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=1, org_id=org_id
    )
    updated = installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=2, org_id=org_id
    )
    assert updated.installation_id == 2


def test_upsert_falls_back_to_update_on_concurrent_sync_race(db):
    # Simulates a concurrent sync: the existence check misses, the insert hits the unique
    # constraint, and upsert must recover by updating the existing row.
    org_id = _acme_org_id(db)
    existing = installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=1, org_id=org_id
    )

    original_first = Query.first
    calls = {"n": 0}

    def racy_first(self):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return original_first(self)

    with patch.object(Query, "first", racy_first):
        result = installation_repo.upsert(
            db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=2, org_id=org_id
        )

    assert result.id == existing.id
    assert result.installation_id == 2


def test_upsert_reraises_when_the_integrity_error_was_not_actually_a_race(db):
    # An FK-violation IntegrityError has no row to fall back to, so it must re-raise.
    with pytest.raises(IntegrityError):
        installation_repo.upsert(
            db,
            account_login="acme",
            account_type="Organization",
            auth_mode="app",
            installation_id=1,
            org_id=999999,
        )


def test_get_for_org_matches_regardless_of_login_casing(db):
    # Ownership checks compare logins case-insensitively, so this lookup must too.
    org_id = _acme_org_id(db)
    installation_repo.upsert(
        db, account_login="Acme", account_type="Organization", auth_mode="app", installation_id=1, org_id=org_id
    )

    found = installation_repo.get_for_org(db, org_id=org_id, account_login="acme")

    assert found is not None
    assert found.installation_id == 1


def test_get_for_user_matches_regardless_of_login_casing(db):
    from src.core.db import User

    user = User(email="dev@example.com", name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    installation_repo.upsert(
        db,
        account_login="Octocat",
        account_type="User",
        auth_mode="app",
        installation_id=7,
        owner_user_id=user.id,
    )

    found = installation_repo.get_for_user(db, owner_user_id=user.id, account_login="octocat")

    assert found is not None
    assert found.installation_id == 7


def test_resync_without_installation_id_keeps_the_stored_id(db):
    org_id = _acme_org_id(db)
    installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=77, org_id=org_id
    )
    row = installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=None, org_id=org_id
    )
    assert row.installation_id == 77


def test_last_uninstall_purges_the_tenants_github_data(db):
    from sqlalchemy import text

    from src.repositories import automation_settings_repo

    org_id = _acme_org_id(db)
    row = installation_repo.upsert(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=88, org_id=org_id
    )
    tenant_id = row.tenant_id
    automation_settings_repo.upsert(db, tenant_id, "acme/api", "dependabot_triage", enabled=True, mode="approve_only")
    db.execute(
        text("INSERT INTO org_members (tenant_id, login, avatar_url, role, added_at) VALUES (:t, 'octo', '', 'member', NOW())"),
        {"t": tenant_id},
    )
    db.commit()

    count, _ = installation_repo.delete_by_installation_id(db, 88)

    assert count == 1
    assert automation_settings_repo.list_for_feature(db, tenant_id, "dependabot_triage") == []
    assert db.execute(text("SELECT count(*) FROM org_members WHERE tenant_id = :t"), {"t": tenant_id}).scalar() == 0
