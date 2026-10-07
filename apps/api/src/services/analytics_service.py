import httpx
from checks.runner import run_all_checks

from src.core.app_config import get_config
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


def instance_scores_hygiene() -> bool:
    return get_config("score_hygiene_checks", "false") == "true"


def org_scores_hygiene(org) -> bool:
    """An org's own setting wins; NULL (never set) follows the instance-wide one."""
    override = getattr(org, "score_hygiene_checks", None)
    return override if override is not None else instance_scores_hygiene()


def get_overview(
    owner: str, token: str, account_type: str = "Organization", score_hygiene: bool | None = None
) -> dict:
    """`score_hygiene` None = the instance-wide setting; callers scanning a Clevis org pass the org's."""
    base_url = settings.github_api_base
    if score_hygiene is None:
        score_hygiene = instance_scores_hygiene()
    # Unscored hygiene checks cost hundreds of GitHub calls per scan for a result that changes nothing.
    report = run_all_checks(
        owner=owner, token=token, base_url=base_url, account_type=account_type, include_hygiene=score_hygiene
    )
    checks = report["checks"]
    # Stamp the policy onto each check: it is persisted with the scan, so later readers (the digest)
    # explain a stored score with the policy it was computed under, not whatever the config says now.
    for c in checks:
        c["scored"] = c["status"] != "not_applicable" and (score_hygiene or not c.get("informational"))
    scored = [c for c in checks if c["scored"]]
    failed = [c for c in scored if c["status"] in ("fail", "error")]
    score = 100 - int((len(failed) / max(1, len(scored))) * 100)
    return {
        "owner": owner,
        "score": score,
        "total_checks": len(scored),
        "failed_checks": len(failed),
        "repo_count": report["repo_count"],
        "checks": checks,
    }
