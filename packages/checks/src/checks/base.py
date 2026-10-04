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

    def run(
        self,
        owner: str,
        token: str,
        base_url: str = "https://api.github.com",
        repos: list | None = None,
        account_type: str = "Organization",
    ) -> dict:
        raise NotImplementedError
