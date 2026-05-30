from checks.runner import run_all_checks

from src.core.config import settings


def get_overview(owner: str, token: str) -> dict:
    base_url = settings.github_api_base
    report = run_all_checks(owner=owner, token=token, base_url=base_url)
    checks = report["checks"]
    failed = [c for c in checks if c["status"] == "fail"]
    score = 100 - int((len(failed) / max(1, len(checks))) * 100)
    return {
        "owner": owner,
        "score": score,
        "total_checks": len(checks),
        "failed_checks": len(failed),
        "checks": checks,
    }
