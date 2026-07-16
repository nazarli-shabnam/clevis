import httpx
from checks.runner import run_all_checks

from src.core.config import settings


def get_account_type(owner: str, token: str, base_url: str | None = None) -> str:
    base_url = base_url or settings.github_api_base
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=20) as client:
        r = client.get(f"{base_url}/users/{owner}", headers=headers)
    r.raise_for_status()
    return r.json().get("type", "User")


def get_overview(owner: str, token: str) -> dict:
    base_url = settings.github_api_base
    report = run_all_checks(owner=owner, token=token, base_url=base_url)
    checks = report["checks"]
    scored = [c for c in checks if c["status"] != "not_applicable"]
    failed = [c for c in scored if c["status"] in ("fail", "error")]
    score = 100 - int((len(failed) / max(1, len(scored))) * 100)
    return {
        "owner": owner,
        "score": score,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "repo_count": report["repo_count"],
        "checks": checks,
    }
