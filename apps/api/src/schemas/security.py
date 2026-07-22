from datetime import datetime

from pydantic import BaseModel


class RepoSecurityRow(BaseModel):
    repo: str
    branch_protection: bool
    secret_scanning: bool
    dependabot_enabled: bool
    dependabot_critical_count: int
    dependabot_high_count: int
    code_scanning: bool
    force_push_allowed: bool
    score: int


class VulnCounts(BaseModel):
    critical: int
    high: int
    medium: int
    low: int


class MatrixSummary(BaseModel):
    fully_compliant_count: int
    critical_risk_count: int
    secret_hits_count: int
    vuln_by_severity: VulnCounts


class SecurityMatrixResponse(BaseModel):
    owner: str
    repos: list[RepoSecurityRow]
    summary: MatrixSummary


class SecretAlert(BaseModel):
    # NOTE: the actual secret value is NEVER included here, only alert metadata.
    number: int
    state: str
    secret_type: str
    secret_type_display: str
    resolved_reason: str | None
    created_at: datetime
    resolved_at: datetime | None
    repo: str
    url: str


class SecretScanningResponse(BaseModel):
    repository: str
    alerts: list[SecretAlert]
