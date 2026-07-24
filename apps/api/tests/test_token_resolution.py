"""Tests for src.services.token_resolution — installation-token-first, PAT-fallback."""

from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from src.core.config import settings
from src.core.db import User
from src.repositories import installation_repo, org_membership_repo, org_repo
from src.services import github_app
from src.services.token_resolution import (
    InsufficientOrgRole,
    NoGitHubTokenAvailable,
    resolve_org_token,
    resolve_owner_token,
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
    with pytest.raises(NoGitHubTokenAvailable, match="Install the GitHub App"):
        resolve_org_token(db, org_id=org.id, account_login="acme", client_token=None)


def test_resolve_org_token_error_distinguishes_mint_failure_from_no_installation(db, app_configured):
    # Regression test for #250: an installation row exists (App was installed), but
    # minting a token for it failed -- the error must say so, not tell the caller to
    # "install" something that's already installed per the DB.
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
        with pytest.raises(NoGitHubTokenAvailable) as exc_info:
            resolve_org_token(db, org_id=org.id, account_login="acme", client_token=None)
    message = str(exc_info.value)
    assert "installation exists" in message
    assert "minting a token" in message
    assert "Install the GitHub App" not in message


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
    with pytest.raises(NoGitHubTokenAvailable, match="Install the GitHub App"):
        resolve_personal_token(db, owner_user_id=user.id, account_login="shabnam", client_token=None)


def test_resolve_personal_token_error_distinguishes_mint_failure_from_no_installation(db, app_configured):
    user = _make_user(db, "shabnam@e.com")
    installation_repo.create(
        db, account_login="shabnam", account_type="User", auth_mode="app", installation_id=7, owner_user_id=user.id
    )
    request = httpx.Request("POST", "https://api.github.com/app/installations/7/access_tokens")
    response = httpx.Response(404, request=request)
    with patch(
        "src.services.token_resolution.github_app.get_installation_token",
        side_effect=httpx.HTTPStatusError("not found", request=request, response=response),
    ):
        with pytest.raises(NoGitHubTokenAvailable) as exc_info:
            resolve_personal_token(db, owner_user_id=user.id, account_login="shabnam", client_token=None)
    message = str(exc_info.value)
    assert "installation exists" in message
    assert "minting a token" in message
    assert "Install the GitHub App" not in message


def test_resolve_owner_token_prefers_org_installation_for_a_member(db, app_configured):
    # Regression test: Overview's /me/* endpoints (cockpit, my-view, overview) could
    # never find an org-only GitHub App installation because they called
    # resolve_personal_token exclusively, which only checks owner_user_id-scoped rows.
    user = _make_user(db, "shabnam@e.com")
    org = org_repo.get_or_create(db, github_login="OpenHikmah")
    org_membership_repo.get_or_create(db, org.id, user.id, role="member")
    installation_repo.create(
        db, account_login="OpenHikmah", account_type="Organization", auth_mode="app", installation_id=99, org_id=org.id
    )
    with patch("src.services.token_resolution.github_app.get_installation_token", return_value="org-token") as mint:
        token = resolve_owner_token(db, user_id=user.id, owner="OpenHikmah", client_token=None)
    mint.assert_called_once_with(99)
    assert token == "org-token"


def test_resolve_owner_token_falls_back_to_personal_when_owner_is_not_a_connected_org(db, app_configured):
    user = _make_user(db, "shabnam@e.com")
    installation_repo.create(
        db, account_login="shabnam", account_type="User", auth_mode="app", installation_id=7, owner_user_id=user.id
    )
    with patch("src.services.token_resolution.github_app.get_installation_token", return_value="personal-token"):
        token = resolve_owner_token(db, user_id=user.id, owner="shabnam", client_token=None)
    assert token == "personal-token"


def test_resolve_owner_token_ignores_org_installation_when_caller_is_not_a_member(db, app_configured):
    # A random authenticated user shouldn't be able to pull another org's GitHub data
    # through a /me/* endpoint just by typing its login.
    user = _make_user(db, "outsider@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    with pytest.raises(NoGitHubTokenAvailable, match="Install the GitHub App"):
        resolve_owner_token(db, user_id=user.id, owner="acme", client_token=None)


def test_resolve_owner_token_requires_admin_role_when_min_role_is_admin(db, app_configured):
    # Cache clear / workflow dispatch are privileged actions -- a plain "member" of the
    # org must not get an org-scoped token through the personal endpoint for those.
    user = _make_user(db, "shabnam@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org.id, user.id, role="member")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    with pytest.raises(InsufficientOrgRole):
        resolve_owner_token(db, user_id=user.id, owner="acme", client_token=None, min_role="admin")


def test_resolve_owner_token_rejects_insufficient_role_even_with_a_client_token(db, app_configured):
    # Regression test (CodeRabbit finding on PR #264): a "member" must not be able to
    # bypass the admin-only gate by supplying their own PAT -- that would let a
    # client-supplied token stand in for the org-admin check AGENTS.md says must never
    # be bypassed for privileged org actions (cache clear, workflow dispatch).
    user = _make_user(db, "shabnam@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org.id, user.id, role="member")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    with pytest.raises(InsufficientOrgRole):
        resolve_owner_token(db, user_id=user.id, owner="acme", client_token="ghp_client", min_role="admin")


def test_resolve_owner_token_matches_org_login_case_insensitively(db, app_configured):
    # Regression test (CodeRabbit finding on PR #264): org_repo.get_by_login is an exact
    # match, while installation_repo.get_for_org is already case-insensitive (#246) --
    # a casing variant of a connected org's login must still resolve to its installation,
    # not silently fall through to the personal-token path.
    user = _make_user(db, "shabnam@e.com")
    org = org_repo.get_or_create(db, github_login="OpenHikmah")
    org_membership_repo.get_or_create(db, org.id, user.id, role="member")
    installation_repo.create(
        db, account_login="OpenHikmah", account_type="Organization", auth_mode="app", installation_id=99, org_id=org.id
    )
    with patch("src.services.token_resolution.github_app.get_installation_token", return_value="org-token") as mint:
        token = resolve_owner_token(db, user_id=user.id, owner="openhikmah", client_token=None)
    mint.assert_called_once_with(99)
    assert token == "org-token"


def test_resolve_owner_token_allows_admin_role_when_min_role_is_admin(db, app_configured):
    user = _make_user(db, "shabnam@e.com")
    org = org_repo.get_or_create(db, github_login="acme")
    org_membership_repo.get_or_create(db, org.id, user.id, role="admin")
    installation_repo.create(
        db, account_login="acme", account_type="Organization", auth_mode="app", installation_id=42, org_id=org.id
    )
    with patch("src.services.token_resolution.github_app.get_installation_token", return_value="org-token"):
        token = resolve_owner_token(db, user_id=user.id, owner="acme", client_token=None, min_role="admin")
    assert token == "org-token"
