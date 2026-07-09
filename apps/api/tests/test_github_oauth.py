"""Unit tests for the GitHub OAuth helpers (no DB, httpx mocked)."""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

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
    state = github_oauth.sign_state()
    assert github_oauth.verify_state(state) is True


def test_state_rejects_tampered_and_expired():
    assert github_oauth.verify_state("not-a-token") is False
    # exp in the past -> invalid
    expired = github_oauth.sign_state(now=1_000_000)
    assert github_oauth.verify_state(expired) is False


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


def test_not_configured_raises(monkeypatch):
    # Force unconfigured regardless of the ambient .env (a real App may be configured locally).
    monkeypatch.setattr(settings, "github_app_client_id", None)
    monkeypatch.setattr(settings, "github_app_client_secret", None)
    with pytest.raises(github_oauth.GitHubOAuthNotConfigured):
        github_oauth.build_authorize_url(state="s", redirect_uri=_REDIRECT)
