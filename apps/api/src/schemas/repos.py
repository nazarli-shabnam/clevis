from pydantic import BaseModel, SecretStr


class RepoListInput(BaseModel):
    # Optional: falls back to a GitHub App installation token when one is connected
    # for this owner (see src.services.token_resolution).
    token: SecretStr | None = None


class RepoSummary(BaseModel):
    name: str
    full_name: str
    private: bool
    language: str | None = None
    stargazers_count: int
    open_issues_count: int
    pushed_at: str | None = None
    default_branch: str
    html_url: str


class RepoListResponse(BaseModel):
    org: str
    total: int
    repos: list[RepoSummary]


class RepoStatsInput(BaseModel):
    token: SecretStr | None = None


class RepoStatsResponse(BaseModel):
    repository: str
    commit_activity: list[dict]
    participation: dict
    contributors: list[dict]


class RepoPullsInput(BaseModel):
    token: SecretStr | None = None
    state: str = "open"


class PullSummary(BaseModel):
    number: int
    title: str
    user: str | None = None
    created_at: str
    html_url: str


class RepoPullsResponse(BaseModel):
    repository: str
    total: int
    pulls: list[PullSummary]
