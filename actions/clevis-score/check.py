#!/usr/bin/env python3
"""Fail a CI job when an org's Clevis score is below a threshold or a named check fails.

Standard library only, so the action needs no install step. Configuration comes from the
environment (set by action.yml); the API token is read from CLEVIS_TOKEN and is never printed.

Exit codes: 0 = gate passed, 1 = gate failed (score/check), 2 = could not get a score.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

_FAILING = {"fail", "error"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib re-sends custom headers (our bearer token) to wherever a redirect points, so never follow one."""

    def redirect_request(self, *args, **kwargs):
        return None


def check_api_url(api_url: str) -> None:
    """The bearer token must only ever travel over https (plain http only for a local dev server)."""
    parts = urllib.parse.urlsplit(api_url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS:
        return
    raise ValueError("api-url must be an https URL")


def evaluate(data: dict, threshold: int, required_checks: list[str]) -> list[str]:
    """Return human-readable gate failures (empty list = pass)."""
    problems: list[str] = []
    score = data.get("score")
    if not isinstance(score, int):
        return ["response did not contain a numeric score"]
    if score < threshold:
        problems.append(f"score {score} is below the threshold {threshold}")
    by_id = {c.get("id"): c for c in data.get("checks", []) if isinstance(c, dict)}
    for check_id in required_checks:
        check = by_id.get(check_id)
        if check is None:
            problems.append(f"required check '{check_id}' was not found in the scan")
        elif check.get("status") in _FAILING:
            problems.append(f"check '{check_id}' ({check.get('title', '')}) is {check['status']}")
    return problems


def _request(method: str, url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "clevis-score-action"},
    )
    with urllib.request.build_opener(_NoRedirect).open(req, timeout=120) as resp:  # scans can take a while
        return json.loads(resp.read().decode())


def fetch_score(api_url: str, org: str, token: str, refresh: bool) -> dict:
    base = f"{api_url.rstrip('/')}/api/v1/orgs/{urllib.parse.quote(org, safe='')}"
    return _request("POST", f"{base}/scan", token) if refresh else _request("GET", f"{base}/score", token)


def main(env: dict[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    api_url, org, token = env.get("CLEVIS_API_URL", ""), env.get("CLEVIS_ORG", ""), env.get("CLEVIS_TOKEN", "")
    if not (api_url and org and token):
        print("CLEVIS_API_URL, CLEVIS_ORG and CLEVIS_TOKEN are required", file=sys.stderr)
        return 2
    try:
        threshold = int(env.get("CLEVIS_THRESHOLD") or 0)
    except ValueError:
        print("threshold must be an integer", file=sys.stderr)
        return 2
    try:
        check_api_url(api_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    required = [c.strip() for c in (env.get("CLEVIS_FAIL_ON_CHECKS") or "").split(",") if c.strip()]
    refresh = (env.get("CLEVIS_REFRESH") or "").lower() == "true"

    try:
        data = fetch_score(api_url, org, token, refresh)
    except urllib.error.HTTPError as exc:
        print(f"Clevis API returned HTTP {exc.code}", file=sys.stderr)
        return 2
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Could not reach the Clevis API: {type(exc).__name__}", file=sys.stderr)
        return 2

    problems = evaluate(data, threshold, required)
    print(f"Clevis score for {org}: {data.get('score')} (threshold {threshold})")
    out = env.get("GITHUB_OUTPUT")
    if out and isinstance(data.get("score"), int):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"score={data['score']}\n")
    for problem in problems:
        print(f"::error::Clevis gate failed: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
