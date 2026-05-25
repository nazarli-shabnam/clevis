from checks.github_checks import BranchProtectionEnabled, OrgMFARequired, SecretScanningEnabled, _get_all_pages


def run_all_checks(owner: str, token: str, base_url: str = "https://api.github.com") -> dict:
    # Fetch repos once and share with all checks that need them (B-05: avoid double pagination)
    repos = _get_all_pages(base_url, f"/orgs/{owner}/repos", token)

    checks = [OrgMFARequired(), BranchProtectionEnabled(), SecretScanningEnabled()]
    results = []
    for check in checks:
        output = check.run(owner=owner, token=token, base_url=base_url, repos=repos)
        results.append({
            "id": check.metadata.check_id,
            "title": check.metadata.title,
            "severity": check.metadata.severity,
            "remediation": check.metadata.remediation,
            "status": output["status"],
            "value": output["value"],
        })
    return {"checks": results}
