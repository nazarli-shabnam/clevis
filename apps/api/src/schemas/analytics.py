from datetime import datetime
from typing import Literal

from pydantic import BaseModel, SecretStr


class AnalyticsInput(BaseModel):
    owner: str
    # Optional: falls back to a GitHub App installation token when one is connected
    # for this owner (see src.services.token_resolution).
    token: SecretStr | None = None


class CheckResult(BaseModel):
    """One security-check result as produced by ``checks.runner.run_all_checks``.

    Typed so a shape drift in ``packages/checks`` fails fast at the API boundary instead
    of silently reaching the UI and crashing a render. ``value`` is a union of every shape
    the six checks and the runner's error paths emit.

    ``severity`` stays a free ``str`` on purpose: it's unconstrained upstream and only a
    cosmetic UI chip -- pinning it here would turn a new check's severity label into a
    500 on the whole overview.
    """

    id: str
    title: str
    severity: str
    remediation: str
    # Hygiene checks: shown, but only scored when the instance opts in.
    informational: bool = False
    # Whether this scan counted the check toward the score (informational checks only do when hygiene
    # scoring is on). None on scans stored before the stamp existed.
    scored: bool | None = None
    status: Literal["pass", "fail", "error", "not_applicable"]
    value: bool | str | dict[str, int] | None = None


class AnalyticsResponse(BaseModel):
    owner: str
    score: int
    total_checks: int
    failed_checks: int
    repo_count: int
    checks: list[CheckResult]


class HygieneScoringUpdate(BaseModel):
    # None clears the org's override so it follows the instance-wide setting again.
    enabled: bool | None


class HygieneScoringSettings(BaseModel):
    enabled: bool | None  # the org's override; None = follows the instance
    effective: bool  # what the next scan will actually do
    instance_default: bool


class ScanHistoryEntry(BaseModel):
    id: int
    owner: str
    score: int
    total_checks: int
    failed_checks: int
    created_at: datetime


class ScanExportEntry(ScanHistoryEntry):
    """A scan-history row plus its full per-check breakdown, for the compliance export.
    ``checks`` is left as a permissive ``list[dict]`` on purpose: this replays historical
    audit data, and a row persisted by an older runner revision must not fail response
    validation and 500 the whole export."""

    checks: list[dict] = []


class ScanExportResponse(BaseModel):
    """Wraps the export rows with a ``truncated`` flag so a windowed audit export
    that hit the row cap is never silently partial."""

    truncated: bool = False
    row_count: int = 0
    entries: list[ScanExportEntry] = []


class OrgEventSummary(BaseModel):
    id: str
    type: str
    actor: str
    actor_avatar: str
    repo: str
    summary: str
    created_at: datetime


class PrWeekBucket(BaseModel):
    week: str
    opened: int
    merged: int


class AtRiskRepo(BaseModel):
    repo: str
    reasons: list[str]
    severity: Literal["warning", "critical"]


class MilestoneSummary(BaseModel):
    repo: str
    title: str
    due_on: datetime | None
    open_issues: int
    closed_issues: int
    progress_pct: float
    state: Literal["on_track", "at_risk", "overdue"]


class PrCycleTimeWeek(BaseModel):
    week: str
    avg_days: float


class CockpitResponse(BaseModel):
    repo_count: int
    # None for a User-type (personal) owner -- personal GitHub accounts have no "members"
    # concept, so there's no fallback numeric value to report; not the same as `degraded`,
    # which flags a live GitHub call that genuinely failed.
    member_count: int | None
    latest_score: int | None
    score_trend: list[int]
    recent_events: list[OrgEventSummary]
    open_pr_count: int
    pr_merge_rate_4w: list[PrWeekBucket]
    commit_activity_4w: list[int]
    total_cache_size_bytes: int
    cache_job_success_rate: float
    commit_heatmap_52w: list[int] = []
    at_risk_repos: list[AtRiskRepo] = []
    milestones: list[MilestoneSummary] = []
    pr_cycle_time_8w: list[PrCycleTimeWeek] = []
    release_cadence_4w: list[int] = []
    commit_activity_source: Literal["github", "aggregate"] = "github"
    recent_events_source: Literal["github", "aggregate"] = "github"
    # True if a stored aggregate is stale relative to gap_heal_stale_hours -- only ever set when
    # recent_events_source == "aggregate"; the live "github" path has no ingestion cursor to be
    # stale against (whatever GitHub returns synchronously is definitionally current).
    recent_events_stale: bool = False
    # True if any best-effort live GitHub call underlying this response failed and fell back to a
    # zero/empty/partial value (see the _safe_* helpers below) -- lets the UI distinguish "this org
    # genuinely has none" from "we couldn't fully fetch this," which previously rendered identically.
    degraded: bool = False
    # Whether any automation has really run for this account's tenant (onboarding checklist). None when it
    # couldn't be determined, so the UI hides that step rather than nagging.
    has_automation_run: bool | None = None


class PRSummary(BaseModel):
    number: int
    title: str
    repository: str
    html_url: str
    updated_at: datetime
    # When the PR was opened -- the review-wait proxy (GitHub search doesn't expose when the
    # review was requested). None only if GitHub omitted it.
    created_at: datetime | None = None
    # CI state of the PR's head commit, filled in only for the user's own open PRs (it costs GitHub
    # calls per PR): "unknown" when it couldn't be determined, None when not looked up.
    ci_status: Literal["passing", "failing", "pending", "unknown"] | None = None


class IssueSummary(BaseModel):
    number: int
    title: str
    repository: str
    html_url: str
    updated_at: datetime


class MyViewResponse(BaseModel):
    my_open_prs: list[PRSummary] = []
    review_requests: list[PRSummary] = []
    assigned_issues: list[IssueSummary] = []
    # True when GitHub's /user (the source of "who am I") couldn't be resolved -- an
    # installation (App) token can't call it, and the signed-in Clevis user has no
    # GitHub-OAuth-linked login to fall back on either. Distinguishes "we don't know who
    # you are on GitHub" from "you genuinely have zero open PRs/reviews/issues", which
    # would otherwise render identically as an empty list.
    identity_unresolved: bool = False
    # True GitHub search totals: the lists above are capped at a handful of rows, so a count
    # shown to the user must come from here, not from len(list).
    my_open_prs_total: int = 0
    review_requests_total: int = 0
    assigned_issues_total: int = 0
    # True when any of the searches failed, so empty lists mean "unknown", not "nothing waiting".
    incomplete: bool = False


class MyPrListResponse(BaseModel):
    items: list[PRSummary] = []
    total_count: int = 0
    page: int = 1
    per_page: int = 25
    identity_unresolved: bool = False


class MyIssueListResponse(BaseModel):
    items: list[IssueSummary] = []
    total_count: int = 0
    page: int = 1
    per_page: int = 25
    identity_unresolved: bool = False


class ActionsUsageResponse(BaseModel):
    """GitHub Actions minutes for the org's current billing month, shaped from
    ``GET /organizations/{org}/settings/billing/usage/summary?product=actions`` (the
    older ``/settings/billing/actions`` endpoint was retired on 2025-09-26).

    Only ``minutes`` line items are counted; Actions storage (GB) is out of scope. The
    usage API reports consumption, not the plan's allowance:

    - ``total_minutes_used`` — all Actions minutes consumed this month
    - ``included_minutes_used`` — the slice covered by the plan's included allowance
      (GitHub's ``discountQuantity``)
    - ``paid_minutes_used`` — the slice billed on top (GitHub's ``netQuantity``)
    """

    total_minutes_used: float = 0
    included_minutes_used: float = 0
    paid_minutes_used: float = 0
    minutes_used_breakdown: dict[str, float] = {}
