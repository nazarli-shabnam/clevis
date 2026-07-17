"""Tests for src.services.token_resolution — installation-token-first, PAT-fallback."""

from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from src.core.config import settings
from src.core.db import User
from src.repositories import installation_repo, org_repo
from src.services import github_app
from src.services.token_resolution import (
    NoGitHubTokenAvailable,
    resolve_org_token,
    resolve_personal_token,
)


def _make_user(db, email: str) -> User:
    user = User(email=email, name=None, password_hash=None, is_workspace_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def app_configured(monkeypatch):
    """Installation-token lookup only kicks in when the GitHub App is configured."""
    monkeypatch.setattr(settings, "github_app_id", "123")
    monkeypatch.setattr(settings, "github_app_private_key", SecretStr("dummy-pem"))


def test_resolve_org_token_uses_installation_when_connected(db, app_configured):
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    with patch("src.services.token_resolution.github_app.get_installation_token", return_value="minted-token") as mint:
        token = resolve_org_token(db, org_id=org.id, account_login="acme", client_token="ghp_client")
    mint.assert_called_once_with(42)
    assert token == "minted-token"


def test_resolve_org_token_falls_back_to_client_token_when_no_installation(db, app_configured):
    org = org_repo.get_or_create(db, github_login="acme")
    token = resolve_org_token(db, org_id=org.id, account_login="acme", client_token="ghp_client")
    assert token == "ghp_client"


def test_resolve_org_token_falls_back_when_app_not_configured(db):
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    token = resolve_org_token(db, org_id=org.id, account_login="acme", client_token="ghp_client")
    assert token == "ghp_client"


def test_resolve_org_token_falls_back_when_get_installation_token_raises_not_configured(db, app_configured):
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    with patch(
        "src.services.token_resolution.github_app.get_installation_token",
        side_effect=github_app.GitHubAppNotConfigured("nope"),
    ):
        token = resolve_org_token(db, org_id=org.id, account_login="acme", client_token="ghp_client")
    assert token == "ghp_client"


def test_resolve_org_token_falls_back_when_installation_token_mint_fails(db, app_configured):
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    request = httpx.Request("POST", "https://api.github.com/app/installations/42/access_tokens")
    response = httpx.Response(404, request=request)
    with patch(
        "src.services.token_resolution.github_app.get_installation_token",
        side_effect=httpx.HTTPStatusError("not found", request=request, response=response),
    ):
        token = resolve_org_token(db, org_id=org.id, account_login="acme", client_token="ghp_client")
    assert token == "ghp_client"


def test_resolve_org_token_raises_when_nothing_available(db, app_configured):
    org = org_repo.get_or_create(db, github_login="acme")
    with pytest.raises(NoGitHubTokenAvailable, match="Connected orgs"):
        resolve_org_token(db, org_id=org.id, account_login="acme", client_token=None)


def test_resolve_personal_token_uses_installation_when_connected(db, app_configured):
    user = _make_user(db, "shabnam@e.com")
    installation_repo.create(
        db, account_login="shabnam", account_type="User", auth_mode="app", installation_id=7, owner_user_id=user.id
    )
    with patch("src.services.token_resolution.github_app.get_installation_token", return_value="minted-token") as mint:
        token = resolve_personal_token(db, owner_user_id=user.id, account_login="shabnam", client_token=None)
    mint.assert_called_once_with(7)
    assert token == "minted-token"


def test_resolve_personal_token_raises_when_nothing_available(db, app_configured):
    user = _make_user(db, "shabnam@e.com")
    with pytest.raises(NoGitHubTokenAvailable, match="Connected orgs"):
        resolve_personal_token(db, owner_user_id=user.id, account_login="shabnam", client_token=None)
