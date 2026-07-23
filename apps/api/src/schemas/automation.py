from datetime import datetime

from pydantic import BaseModel, SecretStr


class WorkflowSummary(BaseModel):
    # Overlay fields (last_run_*) are set via attribute assignment after construction
    # in the workflows router, not the constructor -- validate_assignment ensures the
    # raw GitHub API string still gets coerced to `datetime`.
    model_config = {"validate_assignment": True}

    id: int
    name: str
    path: str
    state: str
    last_run_status: str | None = None
    last_run_conclusion: str | None = None
    last_run_at: datetime | None = None


class WorkflowsResponse(BaseModel):
    repository: str
    workflows: list[WorkflowSummary]


class RunSummary(BaseModel):
    id: int
    name: str | None
    status: str
    conclusion: str | None
    head_branch: str
    created_at: datetime
    duration_ms: int | None = None


class RunsResponse(BaseModel):
    repository: str
    runs: list[RunSummary]


class DispatchInput(BaseModel):
    # Optional: falls back to a GitHub App installation token when one is connected
    # for this owner (see src.services.token_resolution).
    token: SecretStr | None = None
    ref: str
    inputs: dict[str, str] | None = None


class DispatchResponse(BaseModel):
    dispatched: bool
    message: str | None = None
