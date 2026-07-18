"""Tests for src.repositories.org_repo.get_or_create."""

from src.repositories import org_repo


def test_creates_new_org(db):
    org = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    assert org.github_login == "acme"
    assert org.github_org_id == 1


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
    # Regression test: an org renamed on GitHub keeps the same github_org_id but a new
    # github_login. get_or_create must find it by id and update the login, not attempt
    # a second insert that collides with the github_org_id unique constraint.
    original = org_repo.get_or_create(db, github_login="old-name", github_org_id=99)

    renamed = org_repo.get_or_create(db, github_login="new-name", github_org_id=99)

    assert renamed.id == original.id
    assert renamed.github_login == "new-name"
    assert org_repo.get_by_login(db, "old-name") is None
    assert org_repo.get_by_login(db, "new-name").id == original.id


def test_different_orgs_stay_separate(db):
    acme = org_repo.get_or_create(db, github_login="acme", github_org_id=1)
    globex = org_repo.get_or_create(db, github_login="globex", github_org_id=2)
    assert acme.id != globex.id
