"""GitHub OAuth — "Sign in with GitHub" helpers.

Stateless pieces of the OAuth web flow:
  - `sign_state` / `verify_state` — a short-lived CSRF state token (HS256 with AUTH_SECRET), so we
    don't need server-side session storage between the redirect and the callback.
  - `build_authorize_url` — where we send the browser to start the flow.
  - `exchange_code_for_token` — swap the callback `code` for a GitHub *user* access token.
  - `fetch_identity` — read the user's profile + primary verified email.

Find-or-create of the local `users` row lives in the router (it needs the DB). Requires
GITHUB_APP_CLIENT_ID / GITHUB_APP_CLIENT_SECRET (see `src.core.config.Settings`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from src.core.config import settings

_OAUTH_SCOPE = "read:user user:email read:org"
_STATE_TTL_SECONDS = 600  # 10 minutes between /login and /callback
_STATE_ALG = "HS256"
_STATE_PURPOSE = "github_oauth"


class GitHubOAuthNotConfigured(RuntimeError):
    """Raised when the OAuth client id/secret are not configured."""


class GitHubOAuthError(RuntimeError):
    """Raised when GitHub returns an error during the OAuth exchange."""


def _require_client() -> tuple[str, str]:
    client_id = settings.github_app_client_id
    client_secret = settings.github_app_client_secret
    if not client_id or not client_secret:
        raise GitHubOAuthNotConfigured(
            "GITHUB_APP_CLIENT_ID and GITHUB_APP_CLIENT_SECRET must be set for GitHub login"
        )
    return client_id, client_secret.get_secret_value()


def _web_base() -> str:
    """OAuth authorize/token endpoints live on the web host, not the API host."""
    base = settings.github_api_base.rstrip("/")
    if base == "https://api.github.com":
        return "https://github.com"
    if base.endswith("/api/v3"):  # GitHub Enterprise: https://host/api/v3 -> https://host
        return base[: -len("/api/v3")]
    return base


def sign_state(*, now: int | None = None) -> str:
    issued = int(now if now is not None else time.time())
    payload = {"iat": issued, "exp": issued + _STATE_TTL_SECONDS, "purpose": _STATE_PURPOSE}
    return jwt.encode(payload, settings.auth_secret.get_secret_value(), algorithm=_STATE_ALG)


def verify_state(state: str) -> bool:
    try:
        payload = jwt.decode(state, settings.auth_secret.get_secret_value(), algorithms=[_STATE_ALG])
    except jwt.InvalidTokenError:
        return False
    return payload.get("purpose") == _STATE_PURPOSE


def build_authorize_url(*, state: str, redirect_uri: str) -> str:
    client_id, _ = _require_client()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _OAUTH_SCOPE,
        "state": state,
        "allow_signup": "false",
    }
    return f"{_web_base()}/login/oauth/authorize?{urlencode(params)}"


def exchange_code_for_token(code: str, *, redirect_uri: str) -> str:
    """Swap the callback `code` for a GitHub user access token."""
    client_id, client_secret = _require_client()
    url = f"{_web_base()}/login/oauth/access_token"
    with httpx.Client(timeout=20) as client:
        resp = client.post(
            url,
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise GitHubOAuthError(data.get("error_description") or "No access_token in GitHub response")
    return token


@dataclass
class GitHubIdentity:
    github_user_id: int
    login: str
    name: str | None
    email: str
    avatar_url: str | None


def fetch_identity(user_token: str) -> GitHubIdentity:
    """Read the authenticated GitHub user's profile + a verified email."""
    api = settings.github_api_base.rstrip("/")
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=20) as client:
        u = client.get(f"{api}/user", headers=headers)
        u.raise_for_status()
        user = u.json()
        email = user.get("email")
        if not email:
            e = client.get(f"{api}/user/emails", headers=headers)
            e.raise_for_status()
            email = _primary_verified_email(e.json())
    if not email:
        raise GitHubOAuthError("No verified email available from GitHub")
    return GitHubIdentity(
        github_user_id=user["id"],
        login=user["login"],
        name=user.get("name"),
        email=email,
        avatar_url=user.get("avatar_url"),
    )


@dataclass
class GitHubOrgMembership:
    github_org_id: int
    login: str
    role: str  # "admin" | "member"


def list_user_org_memberships(user_token: str) -> list[GitHubOrgMembership]:
    """List the authenticated user's active org memberships, role included (needs read:org
    scope). One endpoint replaces the old list-orgs-then-check-each-role N+1 pattern —
    GET /user/memberships/orgs returns the caller's role per org in a single (paginated)
    call. Follows the Link header so callers in >100 orgs still get the full list.
    """
    api = settings.github_api_base.rstrip("/")
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    memberships: list[GitHubOrgMembership] = []
    url = f"{api}/user/memberships/orgs?state=active&per_page=100"
    with httpx.Client(timeout=20) as client:
        while url:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            for m in resp.json():
                org = m["organization"]
                memberships.append(GitHubOrgMembership(github_org_id=org["id"], login=org["login"], role=m["role"]))
            url = resp.links.get("next", {}).get("url")
    return memberships


def _primary_verified_email(emails: list[dict]) -> str | None:
    for entry in emails:
        if entry.get("primary") and entry.get("verified"):
            return entry.get("email")
    for entry in emails:  # fall back to any verified address
        if entry.get("verified"):
            return entry.get("email")
    return None
