from dataclasses import dataclass


@dataclass
class CheckMetadata:
    check_id: str
    title: str
    severity: str
    remediation: str
    # Shown with the results but excluded from the score unless the instance opts in.
    informational: bool = False


class Check:
    metadata: CheckMetadata
    # False for a check that never reads the `repos` argument (org-level checks). The runner force-fails
    # only the checks that need the prefetched repo list when that prefetch fails.
    requires_repos: bool = True

    def run(
        self,
        owner: str,
        token: str,
        base_url: str = "https://api.github.com",
        repos: list | None = None,
        account_type: str = "Organization",
    ) -> dict:
        raise NotImplementedError
