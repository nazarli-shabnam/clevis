import time
import httpx
from checks.github_checks import retry_delay
from fastapi import HTTPException

from src.core.config import settings

# A 5xx can mean the write was applied before the gateway failed, so only these are retried on one.
# Rate-limit responses (429 / secondary 403) mean nothing was applied and are retried for any method.
_RETRY_5XX_METHODS = ("GET", "HEAD")


def _retry_wait(method: str, resp: httpx.Response, attempt: int) -> float | None:
    """How long to wait before retrying `resp`, or None to surface it. The policy (429 / secondary 403 /
    5xx, honouring Retry-After and X-RateLimit-Reset, bounded) is checks.github_checks.retry_delay, shared
    with the checks package; the last attempt never retries."""
    if attempt >= 2 or (resp.status_code >= 500 and method.upper() not in _RETRY_5XX_METHODS):
        return None
    return retry_delay(resp, attempt)


def github_error(exc: Exception) -> HTTPException:
    """Map an httpx exception raised by GitHubClient into the HTTPException every
    GitHub-proxying router returns to its caller."""
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"GitHub API error: {exc.response.status_code}"
        try:
            message = exc.response.json().get("message")
        except (ValueError, AttributeError):
            message = None
        if message:
            detail = f"{detail}: {message}"
        return HTTPException(status_code=400, detail=detail)
    if isinstance(exc, httpx.RequestError):
        return HTTPException(status_code=503, detail="GitHub API unreachable")
    raise exc


class GitHubClient:
    def __init__(self, token: str, base_url: str | None = None):
        self.base = base_url or settings.github_api_base
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> dict | list:
        url = f"{self.base}{path}"
        with httpx.Client(timeout=20) as client:
            for attempt in range(3):
                try:
                    resp = client.request(method, url, headers=self.headers, params=params, json=json)
                except httpx.RequestError:
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise
                delay = _retry_wait(method, resp, attempt)
                if delay is not None:
                    time.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp.json() if resp.text else {}
        raise RuntimeError("request loop exhausted without returning")

    def request_paginated(self, path: str, params: dict | None = None, items_key: str | None = None) -> list:
        """GET every page of a list endpoint, following the `Link: rel="next"` header.
        `items_key` is for endpoints like /installation/repositories that nest the
        array under a field instead of returning it bare."""
        results: list = []
        url: str | None = f"{self.base}{path}"
        next_params = dict(params or {})
        next_params.setdefault("per_page", 100)
        with httpx.Client(timeout=20) as client:
            while url:
                resp = None
                for attempt in range(3):
                    try:
                        resp = client.get(url, headers=self.headers, params=next_params)
                    except httpx.RequestError:
                        if attempt < 2:
                            time.sleep(2 ** attempt)
                            continue
                        raise
                    delay = _retry_wait("GET", resp, attempt)
                    if delay is not None:
                        time.sleep(delay)
                        continue
                    resp.raise_for_status()
                    break
                body = resp.json()
                results.extend(body[items_key] if items_key else body)
                next_params = {}
                url = None
                for part in resp.headers.get("Link", "").split(","):
                    part = part.strip()
                    if 'rel="next"' in part:
                        url = part.split(";")[0].strip().strip("<>")
        return results


def list_owner_repos(client: "GitHubClient", owner: str, account_type: str) -> list[dict]:
    """Repo list for either a GitHub org or a personal (User-type) account.
    /orgs/{owner}/repos 404s for a User account. A personal account's token from
    resolve_owner_token can be either a minted GitHub App installation token (works
    with /installation/repositories, not with user-to-server endpoints) or a legacy
    PAT (the reverse) -- callers here can't tell which, so try the installation-only
    endpoint first and fall back to /user/repos on an auth-type mismatch (401/403).
    Same contract as checks.runner._fetch_repos, kept separate since this layer uses
    GitHubClient rather than the checks package's own httpx helpers."""
    if account_type != "User":
        return client.request_paginated(f"/orgs/{owner}/repos", params={"type": "all", "sort": "pushed"})
    try:
        return client.request_paginated("/installation/repositories", items_key="repositories")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            # `type` is mutually exclusive with `affiliation` on this endpoint (GitHub answers 422).
            return client.request_paginated("/user/repos", params={"affiliation": "owner", "sort": "pushed"})
        raise
