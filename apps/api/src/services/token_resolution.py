"""Resolve a GitHub API token for a request, preferring a connected GitHub App
installation over a client-supplied personal access token.

If the target org/account has a `github_installations` row with an `installation_id`,
mint a short-lived installation token via `github_app.get_installation_token()`. Otherwise
fall back to whatever token the caller supplied (the legacy PAT path). Raises
`NoGitHubTokenAvailable` when neither is available.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import GitHubInstallation
from src.repositories import installation_repo
from src.services import github_app

logger = logging.getLogger(__name__)


class NoGitHubTokenAvailable(RuntimeError):
    """Raised when no GitHub App installation and no client-supplied token are available."""


def _from_installation(installation: GitHubInstallation | None) -> str | None:
    if installation is None or installation.installation_id is None:
        return None
    try:
        return github_app.get_installation_token(installation.installation_id)
    except github_app.GitHubAppNotConfigured:
        # Deployment-wide: the App isn't set up at all. Distinct from "no installation for
        # this account" — worth a log line since every installation-backed request will hit
        # this until an operator configures GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY.
        logger.warning("GitHub App installation %s exists but the App is not configured", installation.installation_id)
        return None
    except (httpx.HTTPStatusError, httpx.RequestError):
        # The installation row is stale (App uninstalled/suspended on GitHub's side) or
        # GitHub's token-minting endpoint is briefly unreachable. Don't let this become an
        # unhandled 500 — fall back to a client-supplied token same as "no installation".
        logger.warning("Failed to mint an installation token for installation %s", installation.installation_id, exc_info=True)
        return None


def _github_app_configured() -> bool:
    return settings.github_app_id is not None and settings.github_app_private_key is not None


def resolve_org_token(db: Session, *, org_id: int, account_login: str, client_token: str | None) -> str:
    installation = (
        installation_repo.get_for_org(db, org_id=org_id, account_login=account_login)
        if _github_app_configured()
        else None
    )
    token = _from_installation(installation)
    if token:
        return token
    if client_token:
        return client_token
    raise NoGitHubTokenAvailable(
        f"No GitHub App installation found for '{account_login}' and no token was provided. "
        "Install the GitHub App for this organization, or add a personal access token in Settings."
    )


def resolve_personal_token(db: Session, *, owner_user_id: int, account_login: str, client_token: str | None) -> str:
    installation = (
        installation_repo.get_for_user(db, owner_user_id=owner_user_id, account_login=account_login)
        if _github_app_configured()
        else None
    )
    token = _from_installation(installation)
    if token:
        return token
    if client_token:
        return client_token
    raise NoGitHubTokenAvailable(
        f"No GitHub App installation found for '{account_login}' and no token was provided. "
        "Install the GitHub App, or add a personal access token in Settings."
    )
