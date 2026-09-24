"""Tests for src.repositories.org_repo.get_or_create."""

from unittest.mock import patch

from sqlalchemy.orm import Query

from src.core.db import Tenant
from src.repositories import org_repo


def test_creates_new_org(db):
    org = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    assert org.github_login == "acme"
    assert org.github_org_id == 1


def test_creates_new_org_links_a_tenant(db):
    org = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    assert org.tenant_id is not None
    tenant = db.query(Tenant).filter(Tenant.id == org.tenant_id).first()
    assert tenant is not None
    assert tenant.kind == "org"
    assert tenant.org_id == org.id


def test_existing_org_gets_lazily_backfilled_with_a_tenant(db):
    # An org row with tenant_id NULL must self-heal through get_or_create.
    from src.core.db import Org

    org = Org(github_login="acme", github_org_id=1)
    db.add(org)
    db.commit()
    db.refresh(org)
    assert org.tenant_id is None

    resolved = org_repo.get_or_create(db, github_login="acme", github_org_id=1)

    assert resolved.tenant_id is not None


def test_idempotent_by_login(db):
    first = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    second = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    assert second.id == first.id


def test_backfills_github_org_id_on_existing_login_only_row(db):
    existing = org_repo.get_or_create(db, github_login="acme")
    assert existing.github_org_id is None
    updated = org_repo.get_or_create(db, github_login="acme", github_org_id=42)
    assert updated.id == existing.id
    assert updated.github_org_id == 42


def test_org_rename_resolves_by_github_org_id_instead_of_crashing(db):
    # A renamed org keeps its github_org_id: find it by id and update the login, not re-insert.
    original = org_repo.get_or_create(db, github_login="old-name", github_org_id=99)

    renamed = org_repo.get_or_create(db, github_login="new-name", github_org_id=99)

    assert renamed.id == original.id
    assert renamed.github_login == "new-name"
    assert org_repo.get_by_login(db, "old-name") is None
    assert org_repo.get_by_login(db, "new-name").id == original.id


def test_get_or_create_falls_back_to_org_id_lookup_on_concurrent_insert_race(db):
    # Simulates a concurrent get_or_create: the insert collides on github_org_id, get_by_login
    # misses (different login), so recovery must fall back to get_by_org_id.
    existing = org_repo.get_or_create(db, github_login="old-name", github_org_id=7)

    original_first = Query.first
    calls = {"n": 0}

    def racy_first(self):
        calls["n"] += 1
        # Call 1 (get_by_login) passes through; call 2 (pre-insert get_by_org_id) fakes a miss.
        if calls["n"] == 2:
            return None
        return original_first(self)

    with patch.object(Query, "first", racy_first):
        result = org_repo.get_or_create(db, github_login="new-name-2", github_org_id=7)

    assert result.id == existing.id


def test_different_orgs_stay_separate(db):
    # Capture acme.id now: globex's commit expires acme, and a lazy reload under globex's
    # tenant context would be hidden by RLS.
    acme = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    acme_id = acme.id
    globex = org_repo.get_or_create(db, github_login="globex", github_org_id=2)
    assert acme_id != globex.id
