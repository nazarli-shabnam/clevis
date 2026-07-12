"""Tests for GitHub App webhook endpoint."""

import hashlib
import hmac
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.core.auth import UserOut
from src.core.config import settings
from src.core.db import User, get_db
from src.repositories import installation_repo, org_membership_repo, org_repo
from src.routers.webhooks import router as webhooks_router


def _make_user(db, email: str) -> UserOut:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=False)


@pytest.fixture()
def acme_org(db):
    admin = _make_user(db, "admin@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org_id=org.id, user_id=admin.id, role="admin")
    return {"org": org, "admin": admin}


@pytest.fixture()
def webhook_client(db, monkeypatch):
    monkeypatch.setattr(settings, "github_app_webhook_secret", SecretStr("test-secret"))
    app = FastAPI()
    app.include_router(webhooks_router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _signed_headers(body: bytes, secret: str = "test-secret") -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Event": "installation",
    }


def test_webhook_rejects_invalid_signature(webhook_client):
    body = json.dumps({"action": "deleted", "installation": {"id": 1}}).encode()
    resp = webhook_client.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "installation"})
    assert resp.status_code == 401


def test_webhook_deletes_installation_on_deleted_event(webhook_client, db, acme_org):
    installation_repo.create(
        db,
        account_login="acme",
        account_type="Organization",
        auth_mode="app",
        installation_id=555,
        org_id=acme_org["org"].id,
    )
    body = json.dumps({"action": "deleted", "installation": {"id": 555}}).encode()
    resp = webhook_client.post("/webhooks/github", content=body, headers=_signed_headers(body))
    assert resp.status_code == 204
    assert installation_repo.list_for_org(db, org_id=acme_org["org"].id) == []
