"""Tests for src.repositories.installation_repo.upsert, focused on the concurrent-sync
race path (see apps/api/tests/test_installations.py for router-level coverage)."""

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
    # Simulates two near-simultaneous syncs for the same org/account: this call's initial
    # existence check misses (the other request's row hasn't committed yet from this call's
    # point of view), so it attempts an insert -- which collides on the real unique
    # constraint once the other request's commit has actually landed. Must recover by
    # updating the row that's actually there, not raise an unhandled IntegrityError.
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
    # A different IntegrityError (here: a foreign-key violation from a nonexistent org_id)
    # also lands in the except block, but the re-query genuinely finds nothing to fall back
    # to (no row was ever inserted) -- must re-raise rather than silently swallow it.
    with pytest.raises(IntegrityError):
        installation_repo.upsert(
            db,
            account_login="acme",
            account_type="Organization",
            auth_mode="app",
            installation_id=1,
            org_id=999999,
        )
