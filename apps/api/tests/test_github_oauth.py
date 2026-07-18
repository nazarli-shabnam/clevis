"""Unit tests for the GitHub OAuth helpers (no DB, httpx mocked)."""

import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from pydantic import SecretStr

from src.core.config import settings
from src.services import github_oauth

_CLIENT_ID = "Iv1.abc123"
_REDIRECT = "https://api.example.com/auth/github/callback"


@pytest.fixture
def oauth_configured(monkeypatch):
    monkeypatch.setattr(settings, "github_app_client_id", _CLIENT_ID)
    monkeypatch.setattr(settings, "github_app_client_secret", SecretStr("shh-secret"))
    yield


def test_state_roundtrip():
    state, nonce = github_oauth.sign_state()
    assert github_oauth.verify_state(state, cookie_nonce=nonce) is True


def test_state_rejects_tampered_and_expired():
    assert github_oauth.verify_state("not-a-token", cookie_nonce="whatever") is False
    # exp in the past -> invalid
    expired, nonce = github_oauth.sign_state(now=1_000_000)
    assert github_oauth.verify_state(expired, cookie_nonce=nonce) is False


def test_state_rejects_wrong_purpose():
    # A token signed with our own secret but for a different purpose (or forged with a
    # mismatched purpose claim) must not validate as an OAuth state.
    now = int(time.time())
    other_purpose_token = jwt.encode(
        {"iat": now, "exp": now + 600, "purpose": "not-github-oauth", "nonce": "n"},
        settings.auth_secret.get_secret_value(),
        algorithm="HS256",
    )
    assert github_oauth.verify_state(other_purpose_token, cookie_nonce="n") is False


def test_state_rejects_missing_or_mismatched_cookie_nonce():
    # Regression test for the OAuth login-CSRF gap: a validly-signed, unexpired state
    # must still be rejected if it isn't paired with the browser's own nonce cookie.
    state, nonce = github_oauth.sign_state()
    assert github_oauth.verify_state(state, cookie_nonce=None) is False
    assert github_oauth.verify_state(state, cookie_nonce="some-other-nonce") is False
    # A state token minted for a *different* flow (different nonce) must not validate
    # against this browser's cookie either.
    other_state, _ = github_oauth.sign_state()
    assert github_oauth.verify_state(other_state, cookie_nonce=nonce) is False


def test_build_authorize_url(oauth_configured):
    url = github_oauth.build_authorize_url(state="st", redirect_uri=_REDIRECT)
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == [_CLIENT_ID]
    assert qs["redirect_uri"] == [_REDIRECT]
    assert qs["scope"] == ["read:user user:email read:org"]
    assert qs["state"] == ["st"]


def test_web_base_enterprise(monkeypatch):
    monkeypatch.setattr(settings, "github_api_base", "https://ghe.acme.com/api/v3")
    assert github_oauth._web_base() == "https://ghe.acme.com"


def test_exchange_code_success(oauth_configured):
    with patch("src.services.github_oauth.httpx.Client") as mock_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post = MagicMock(return_value=MagicMock(json=MagicMock(return_value={"access_token": "gho_x"})))
        mock_cls.return_value = client
        token = github_oauth.exchange_code_for_token("code123", redirect_uri=_REDIRECT)
    assert token == "gho_x"


def test_exchange_code_error_raises(oauth_configured):
    with patch("src.services.github_oauth.httpx.Client") as mock_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.post = MagicMock(
            return_value=MagicMock(json=MagicMock(return_value={"error": "bad_verification_code", "error_description": "nope"}))
        )
        mock_cls.return_value = client
        with pytest.raises(github_oauth.GitHubOAuthError):
            github_oauth.exchange_code_for_token("bad", redirect_uri=_REDIRECT)


def test_fetch_identity_uses_profile_email():
    with patch("src.services.github_oauth.httpx.Client") as mock_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get = MagicMock(
            return_value=MagicMock(
                json=MagicMock(
                    return_value={"id": 42, "login": "octocat", "name": "Octo", "email": "octo@example.com", "avatar_url": "http://a/x.png"}
                )
            )
        )
        mock_cls.return_value = client
        ident = github_oauth.fetch_identity("gho_x")
    assert ident.github_user_id == 42
    assert ident.login == "octocat"
    assert ident.email == "octo@example.com"
    # profile email present -> /user/emails not needed
    assert client.get.call_count == 1


def test_fetch_identity_falls_back_to_emails_endpoint():
    profile = MagicMock(json=MagicMock(return_value={"id": 7, "login": "no-email", "name": None, "email": None, "avatar_url": None}))
    emails = MagicMock(
        json=MagicMock(
            return_value=[
                {"email": "secondary@example.com", "primary": False, "verified": True},
                {"email": "main@example.com", "primary": True, "verified": True},
            ]
        )
    )
    with patch("src.services.github_oauth.httpx.Client") as mock_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get = MagicMock(side_effect=[profile, emails])
        mock_cls.return_value = client
        ident = github_oauth.fetch_identity("gho_x")
    assert ident.email == "main@example.com"
    assert client.get.call_count == 2


def test_list_user_org_memberships_follows_pagination():
    page1 = MagicMock(
        links={"next": {"url": "https://api.github.com/user/memberships/orgs?page=2"}},
        json=MagicMock(
            return_value=[
                {"role": "admin", "organization": {"id": 1, "login": "acme"}},
            ]
        ),
    )
    page2 = MagicMock(
        links={},
        json=MagicMock(
            return_value=[
                {"role": "member", "organization": {"id": 2, "login": "other-org"}},
            ]
        ),
    )
    with patch("src.services.github_oauth.httpx.Client") as mock_cls:
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get = MagicMock(side_effect=[page1, page2])
        mock_cls.return_value = client
        memberships = github_oauth.list_user_org_memberships("gho_x")
    assert client.get.call_count == 2
    assert [(m.login, m.role) for m in memberships] == [("acme", "admin"), ("other-org", "member")]


def test_not_configured_raises(monkeypatch):
    # Force unconfigured regardless of the ambient .env (a real App may be configured locally).
    monkeypatch.setattr(settings, "github_app_client_id", None)
    monkeypatch.setattr(settings, "github_app_client_secret", None)
    with pytest.raises(github_oauth.GitHubOAuthNotConfigured):
        github_oauth.build_authorize_url(state="s", redirect_uri=_REDIRECT)
