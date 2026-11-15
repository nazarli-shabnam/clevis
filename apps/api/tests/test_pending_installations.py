"""installation.created is recorded as a notice (pending_installations) and offered to the people it concerns (#1014).

It never creates an org, a membership or a github_installations row: connecting still goes through the
authenticated, GitHub-verified sync endpoint."""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import GitHubInstallation, Org, PendingInstallation, User, get_db
from src.core.rate_limit import _account_buckets
from src.repositories import installation_repo, org_repo
from src.routers.installations import router as inst_router
from src.routers.webhooks import router as webhooks_router

_SECRET = "pending-secret"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    _account_buckets.clear()
    yield
    _account_buckets.clear()


@pytest.fixture()
def hook(db, monkeypatch):
    monkeypatch.setattr(settings, "github_app_webhook_secret", SecretStr(_SECRET))
    app = FastAPI()
    app.include_router(webhooks_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _created(hook, *, installation_id=501, login="acme", account_type="Organization", sender=None, requester=None, action="created"):
    payload = {
        "action": action,
        "installation": {"id": installation_id, "account": {"login": login, "id": 9000, "type": account_type}},
        "sender": sender if sender is not None else {"id": 11, "login": "owner"},
    }
    if requester is not None:
        payload["requester"] = requester
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hook.post(
        "/webhooks/github",
        content=body,
        headers={"Content-Type": "application/json", "X-GitHub-Event": "installation", "X-Hub-Signature-256": sig},
    )


def _user(db, email, github_user_id=None, github_login=None) -> UserOut:
    row = User(
        email=email, name=None, password_hash=None, is_workspace_admin=False,
        github_user_id=github_user_id, github_login=github_login,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return UserOut(id=row.id, email=row.email, name=None, is_workspace_admin=False, github_login=github_login)


def _client(db, user):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    db.execute(text(f"SET app.user_id = {user.id}"))
    return TestClient(app)


def test_an_org_installation_is_recorded_as_a_notice_and_creates_nothing_else(db, hook):
    resp = _created(hook, sender={"id": 11, "login": "owner"}, requester={"id": 22, "login": "member"})

    assert resp.status_code == 200
    row = db.get(PendingInstallation, 501)
    assert (row.account_login, row.account_id) == ("acme", 9000)
    assert (row.sender_github_id, row.sender_login) == (11, "owner")
    assert (row.requester_github_id, row.requester_login) == (22, "member")
    # A notice board only: no org, no installation row, no membership.
    assert db.query(Org).count() == 0 and db.query(GitHubInstallation).count() == 0


def test_a_repeated_delivery_refreshes_one_notice(db, hook):
    _created(hook, sender={"id": 11, "login": "owner"})
    _created(hook, sender={"id": 12, "login": "other-owner"}, requester={"id": 22, "login": "member"})
    [row] = db.query(PendingInstallation).all()
    assert (row.sender_github_id, row.requester_github_id) == (12, 22)


def test_personal_malformed_and_already_connected_installations_are_not_recorded(db, hook):
    _created(hook, installation_id=1, account_type="User")
    _created(hook, installation_id=2, login="")
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=3, org_id=org.id
    )
    _created(hook, installation_id=3)
    # Wrong types are ignored rather than stored (a bool is not an id).
    body = json.dumps({"action": "created", "installation": {"id": True, "account": {"login": "x", "type": "Organization"}}}).encode()
    sig = "sha256=" + hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    hook.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": sig, "Content-Type": "application/json"})

    assert db.query(PendingInstallation).count() == 0


def test_an_unsigned_installation_created_is_rejected_and_stores_nothing(db, hook):
    body = json.dumps({"action": "created", "installation": {"id": 9, "account": {"login": "x", "type": "Organization"}}}).encode()
    resp = hook.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": "sha256=bad"})
    assert resp.status_code == 401
    assert db.query(PendingInstallation).count() == 0


def test_installation_deleted_forgets_the_notice_and_old_notices_are_purged(db, hook):
    old = PendingInstallation(installation_id=70, account_login="old")
    db.add(old)
    db.commit()
    db.execute(text("UPDATE pending_installations SET created_at = now() - interval '31 days' WHERE installation_id = 70"))
    db.commit()
    _created(hook, installation_id=501)  # recording a new one drops notices past their age limit
    assert [r.installation_id for r in db.query(PendingInstallation).all()] == [501]

    resp = _created(hook, installation_id=501, action="deleted")
    assert resp.status_code == 200
    assert db.query(PendingInstallation).count() == 0


def test_the_approver_and_the_requester_each_see_their_own_notice_and_nobody_else_does(db, hook):
    _created(hook, sender={"id": 11, "login": "owner"}, requester={"id": 22, "login": "member"})
    owner = _user(db, "owner@e.com", github_user_id=11, github_login="owner")
    member = _user(db, "member@e.com", github_user_id=22, github_login="member")
    stranger = _user(db, "stranger@e.com", github_user_id=33, github_login="stranger")
    password_only = _user(db, "pw@e.com")

    as_owner = _client(db, owner).get("/me/pending-installations").json()
    as_member = _client(db, member).get("/me/pending-installations").json()

    assert [(r["installation_id"], r["account_login"], r["role"]) for r in as_owner] == [(501, "acme", "approver")]
    assert [(r["installation_id"], r["account_login"], r["role"]) for r in as_member] == [(501, "acme", "requester")]
    assert _client(db, stranger).get("/me/pending-installations").json() == []
    # No linked GitHub account, nothing to match on.
    assert _client(db, password_only).get("/me/pending-installations").json() == []


def test_a_notice_past_its_age_limit_is_not_listed_even_before_any_purge(db, hook):
    _created(hook, sender={"id": 11, "login": "owner"})
    owner = _user(db, "owner@e.com", github_user_id=11, github_login="owner")
    db.execute(text("UPDATE pending_installations SET created_at = now() - interval '31 days'"))
    db.commit()
    assert _client(db, owner).get("/me/pending-installations").json() == []


def test_a_notice_for_an_installation_connected_since_is_not_listed(db, hook):
    _created(hook, sender={"id": 11, "login": "owner"})
    owner = _user(db, "owner@e.com", github_user_id=11, github_login="owner")
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=501, org_id=org.id
    )
    assert _client(db, owner).get("/me/pending-installations").json() == []


def test_connecting_through_the_sync_endpoint_clears_the_notice(db, hook):
    _created(hook, sender={"id": 11, "login": "owner"})
    owner = _user(db, "owner@e.com", github_user_id=11, github_login="owner")
    org = org_repo.get_or_create(db, github_login="acme")
    from src.repositories import org_membership_repo

    org_membership_repo.get_or_create(db, org_id=org.id, user_id=owner.id, role="admin")
    verified = {"account": {"login": "acme", "type": "Organization", "id": 9000}, "permissions": {}}
    with patch("src.routers.installations._verify_installation", return_value=verified), patch(
        "src.routers.installations._capture_permissions_best_effort"
    ), patch("src.routers.installations._enqueue_backfill_best_effort"):
        resp = _client(db, owner).post(
            "/orgs/acme/installations/sync",
            json={"account_login": "acme", "account_type": "Organization", "installation_id": 501},
        )
    assert resp.status_code == 200, resp.text
    assert db.query(PendingInstallation).count() == 0
