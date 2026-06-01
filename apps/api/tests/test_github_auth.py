"""Tests for the GitHub OAuth router + find-or-create (DB-backed)."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from src.core.config import settings
from src.core.db import User, get_db
from src.routers.github_auth import find_or_create_user
from src.routers.github_auth import router as gh_router
from src.services.github_oauth import GitHubIdentity


@pytest.fixture()
def gh_app(db):
    a = FastAPI()
    a.include_router(gh_router, prefix="/auth/github")
    a.dependency_overrides[get_db] = lambda: db
    return a


@pytest.fixture()
def gh_client(gh_app):
    return TestClient(gh_app)


@pytest.fixture()
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "github_app_client_id", "Iv1.abc")
    monkeypatch.setattr(settings, "github_app_client_secret", SecretStr("secret"))


def _identity(**kw) -> GitHubIdentity:
    base = dict(github_user_id=1001, login="octocat", name="Octo", email="octo@example.com", avatar_url=None)
    base.update(kw)
    return GitHubIdentity(**base)


# ── find_or_create_user ─────────────────────────────────────────────────────────

def test_first_user_becomes_owner(db):
    user = find_or_create_user(db, _identity())
    assert user.is_owner is True
    assert user.github_user_id == 1001
    assert user.password_hash is None


def test_second_user_is_member(db):
    find_or_create_user(db, _identity())
    second = find_or_create_user(db, _identity(github_user_id=2002, login="hubot", email="hubot@example.com"))
    assert second.is_owner is False


def test_links_existing_email_user_and_keeps_role(db):
    existing = User(email="owner@example.com", name="Owner", password_hash="x", is_owner=True)
    db.add(existing)
    db.commit()
    db.refresh(existing)
    linked = find_or_create_user(db, _identity(github_user_id=555, email="owner@example.com"))
    assert linked.id == existing.id
    assert linked.github_user_id == 555
    assert linked.is_owner is True


def test_idempotent_by_github_id(db):
    first = find_or_create_user(db, _identity())
    again = find_or_create_user(db, _identity(name="Octo Updated"))
    assert again.id == first.id
    assert db.query(User).count() == 1


# ── endpoints ─────────────────────────────────────────────────────────────────

def test_login_redirects_to_github(gh_client, oauth_configured):
    resp = gh_client.get("/auth/github/login", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].startswith("https://github.com/login/oauth/authorize")


def test_login_unconfigured_returns_503(gh_client):
    resp = gh_client.get("/auth/github/login", follow_redirects=False)
    assert resp.status_code == 503


def test_callback_creates_user_and_sets_cookie(gh_client, db):
    with (
        patch("src.routers.github_auth.github_oauth.verify_state", return_value=True),
        patch("src.routers.github_auth.github_oauth.exchange_code_for_token", return_value="gho_x"),
        patch("src.routers.github_auth.github_oauth.fetch_identity", return_value=_identity()),
    ):
        resp = gh_client.get("/auth/github/callback?code=c&state=s", follow_redirects=False)
    assert resp.status_code == 303
    assert "clevis_session" in resp.headers.get("set-cookie", "")
    assert db.query(User).filter(User.github_user_id == 1001).first() is not None


def test_callback_rejects_bad_state(gh_client):
    with patch("src.routers.github_auth.github_oauth.verify_state", return_value=False):
        resp = gh_client.get("/auth/github/callback?code=c&state=bad", follow_redirects=False)
    assert resp.status_code == 400
