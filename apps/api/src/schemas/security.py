from datetime import datetime
from typing import Literal

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
    # "aggregate" when dependabot and/or code_scanning came from security_alerts instead
    # of a live per-repo GitHub call -- the two dimensions are gated independently, so
    # this is "aggregate" if either one was. branch_protection/force_push/secret_scanning
    # have no ingested event and stay live either way.
    alerts_source: Literal["github", "aggregate"] = "github"
    # Dimension names the token couldn't evaluate (403/429/network error) rather than
    # genuinely observed -- excluded from `score`'s denominator so an unseeable repo isn't
    # scored as clean. Never includes a genuine 404 ("feature is off"), a real negative.
    unknown_dimensions: list[str] = []


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
    # None when no usable link exists -- the aggregate path (security_alerts) doesn't
    # store GitHub's html_url; "" would render every such alert as a broken link.
    url: str | None


class SecretScanningResponse(BaseModel):
    repository: str
    alerts: list[SecretAlert]
    source: Literal["github", "aggregate"] = "github"


class BurndownSeverity(BaseModel):
    severity: str
    open: int
    median_age_days: float | None = None
    oldest_age_days: float | None = None
    # None = no SLA configured for this severity (only critical and high have one).
    sla_days: int | None = None
    breaches: int


class BurndownRepo(BaseModel):
    repo: str
    open: dict[str, int]
    breaches: int
    oldest_age_days: float
    # Link target: the oldest open alert, preferring one that breaches its SLA.
    oldest_alert_number: int


class BurndownPoint(BaseModel):
    date: str
    critical: int
    high: int
    medium: int
    low: int


class DependabotBurndownResponse(BaseModel):
    window_days: int
    severities: list[BurndownSeverity]
    repos: list[BurndownRepo]
    trend: list[BurndownPoint]
