"""Tests that installation sync exercises GitHub App token minting."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import UserOut, require_auth
from src.core.db import User, get_db
from src.repositories import org_membership_repo, org_repo
from src.routers.installations import router as inst_router


def _make_user(db, email: str, github_login: str | None = None) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False, github_login=github_login)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


def _client(db, user):
    app = FastAPI()
    app.include_router(inst_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: user
    return TestClient(app)


def test_org_sync_mints_installation_token(db):
    admin = _make_user(db, "admin@e.com")
    org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org_repo.get_or_create(db, github_login="acme").id, user_id=admin.id, role="admin")
    payload = {
        "account_login": "acme",
        "account_type": "Organization",
        "installation_id": 99,
        "auth_mode": "app",
    }
    with patch("src.routers.installations.github_app.get_installation_token") as mint:
        resp = _client(db, admin).post("/orgs/acme/installations/sync", json=payload)
    assert resp.status_code == 200
    mint.assert_called_once_with(99)
