from collections.abc import Generator
from datetime import date, datetime
import logging

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from src.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class GitHubInstallation(Base):
    __tablename__ = "github_installations"
    __table_args__ = (
        CheckConstraint(
            "(org_id IS NOT NULL AND owner_user_id IS NULL) "
            "OR (org_id IS NULL AND owner_user_id IS NOT NULL)",
            name="ck_github_installations_org_xor_owner",
        ),
        Index(
            "uq_github_installations_org_account",
            "org_id",
            "account_login",
            unique=True,
            postgresql_where="org_id IS NOT NULL",
        ),
        Index(
            "uq_github_installations_user_account",
            "owner_user_id",
            "account_login",
            unique=True,
            postgresql_where="owner_user_id IS NOT NULL",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_login: Mapped[str] = mapped_column(String, nullable=False)
    account_type: Mapped[str] = mapped_column(String, nullable=False)
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auth_mode: Mapped[str] = mapped_column(String, nullable=False)
    token_ref: Mapped[str] = mapped_column(String, nullable=False)
    # Exactly one of org_id / owner_user_id is set: org-connected installs vs. personal installs.
    org_id: Mapped[int | None] = mapped_column(ForeignKey("orgs.id"), nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Installation `permissions` as last observed; NULL = never permission-checked
    # (see src.services.app_permissions).
    granted_permissions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    permissions_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable: actor/target are free text, so pre-migration rows can't be attributed to a tenant.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_job_type", "status", "job_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # One shared cap for crash-reclaims and transient-failure requeues, so a job can't retry forever.
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # Touched by the worker's _JobHeartbeat every ~10s so _reclaim_stale_jobs can tell slow-but-alive
    # from crashed; updated_at only changes at claim and finish.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookDelivery(Base):
    """Durable landing spot for verified GitHub webhook payloads before Redis Streams enqueue.

    No RLS: written with no tenant context and read across tenants. Access control is HMAC
    verification at the receiver."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_tenant_id", "tenant_id"),
        Index("ix_webhook_deliveries_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Nullable: resolution can fail (e.g. org uninstalled between delivery and processing).
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)
    # X-GitHub-Delivery. Not unique: GitHub redelivers the same id on retry; the event consumer dedupes.
    delivery_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)  # X-GitHub-Event
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Exact verified raw bytes, so what HMAC was checked against is preserved for re-verification.
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # queued | queue_failed (XADD failed, retried by webhook_requeue_sweep) | queue_abandoned
    # (past max retry age, never retried again).
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RepoEvent(Base):
    """Normalized, deduplicated event store, populated by the worker's Redis Streams consumer.

    Strict tenant_id RLS (no OR-NULL): the consumer skips deliveries with a null tenant_id."""

    __tablename__ = "repo_events"
    __table_args__ = (
        Index("ix_repo_events_tenant_id", "tenant_id"),
        UniqueConstraint("delivery_id", name="uq_repo_events_delivery_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    # Idempotency key: ON CONFLICT DO NOTHING here makes re-normalizing a redelivery safe.
    delivery_id: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    actor_avatar: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    # The event's own timestamp where the payload has one, else the delivery's received_at.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RepoEventDailyCount(Base):
    """Per-(tenant, repo, event_type, day) rollup, upserted in the same transaction as each RepoEvent.

    Only counted when the RepoEvent insert actually happened, so redeliveries don't double-count."""

    __tablename__ = "repo_event_daily_counts"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "repo", "event_type", "day", name="pk_repo_event_daily_counts"),
        Index("ix_repo_event_daily_counts_tenant_id_day", "tenant_id", "day"),
    )

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class SecurityAlert(Base):
    """Normalized Dependabot/code-scanning/secret-scanning alert state (``kind`` discriminates).

    Upserted, not insert-and-skip: a redelivered alert webhook reflects a real state transition."""

    __tablename__ = "security_alerts"
    __table_args__ = (
        Index("ix_security_alerts_tenant_id", "tenant_id"),
        Index("ix_security_alerts_tenant_id_repo", "tenant_id", "repo"),
        UniqueConstraint("tenant_id", "repo", "kind", "number", name="uq_security_alerts_tenant_repo_kind_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrgMember(Base):
    """Current org membership (row deleted on member_removed), populated from `organization` webhooks.

    No webhook covers later role changes, so `role` can drift until the reconciliation poll corrects it."""

    __tablename__ = "org_members"
    __table_args__ = (
        Index("ix_org_members_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "login", name="uq_org_members_tenant_login"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    login: Mapped[str] = mapped_column(String, nullable=False)
    avatar_url: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default="member")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL = never polled. Only the reconciliation poll sets this (no webhook covers 2FA); a False
    # default would misrepresent an un-polled row as confirmed "2FA off".
    two_factor_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class RepoCollaborator(Base):
    """Current direct repo access grants (row deleted on `removed`), populated from `member` webhooks.

    `source` is only 'direct' for now; team-based access ('team') is deferred."""

    __tablename__ = "repo_collaborators"
    __table_args__ = (
        Index("ix_repo_collaborators_tenant_id", "tenant_id"),
        Index("ix_repo_collaborators_tenant_id_repo", "tenant_id", "repo"),
        UniqueConstraint("tenant_id", "repo", "login", name="uq_repo_collaborators_tenant_repo_login"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    login: Mapped[str] = mapped_column(String, nullable=False)
    permission: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default="direct")
    # NULL = not yet known: the `member` event alone can't determine org membership.
    is_outside_collaborator: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActivitySyncCursor(Base):
    """Per-tenant event-backfill watermark, upserted after each successful backfill run.

    Per-tenant, not per-repo: GitHub's Events API is org/user-scoped. No row until the first sync."""

    __tablename__ = "activity_sync_cursors"

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    account_login: Mapped[str] = mapped_column(String, nullable=False)
    account_type: Mapped[str] = mapped_column(String, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OrgMembershipSyncCursor(Base):
    """Per-org-tenant watermark for the membership reconciliation poll; mirrors ActivitySyncCursor.

    org_login is cached here because the sweep has no payload to read it from."""

    __tablename__ = "org_membership_sync_cursors"

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    org_login: Mapped[str] = mapped_column(String, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SavedToken(Base):
    __tablename__ = "saved_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Best-effort backfill matched on free-text org; nullable since renamed/deleted orgs won't match.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Case-insensitive uniqueness needs a functional index; callers must compare/insert lowercased.
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Null for users who only sign in with GitHub (no email/password credential).
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_workspace_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Bumped by POST /auth/me/revoke-sessions to invalidate all previously issued JWTs.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # GitHub identity (set when the user links / signs in via GitHub OAuth).
    github_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    github_login: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # True for GitHub-linked and first-run setup accounts (email trusted); self-registered accounts
    # start False until they click the verification link.
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_verify_token: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    email_verify_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Org(Base):
    __tablename__ = "orgs"
    __table_args__ = (
        # Composite FK: tenant_id must name a tenant whose org_id is this org (not a personal tenant,
        # another org's, or one shared across orgs).
        ForeignKeyConstraint(["tenant_id", "id"], ["tenants.id", "tenants.org_id"], name="fk_orgs_tenant_id_reciprocal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Nullable: filled lazily on a member's next auth via the GitHub membership check.
    github_org_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    github_login: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Stays nullable: a new Org and its Tenant reference each other, and NOT NULL can't be deferred
    # like a FK can. org_repo.get_or_create self-heals missing values.
    tenant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)




class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'org' AND org_id IS NOT NULL AND personal_user_id IS NULL) "
            "OR (kind = 'personal' AND org_id IS NULL AND personal_user_id IS NOT NULL)",
            name="ck_tenants_kind_xor",
        ),
        Index("uq_tenants_org_id", "org_id", unique=True, postgresql_where="org_id IS NOT NULL"),
        Index(
            "uq_tenants_personal_user_id",
            "personal_user_id",
            unique=True,
            postgresql_where="personal_user_id IS NOT NULL",
        ),
        # Redundant with the PK, but required so orgs.tenant_id can declare a composite FK to
        # (id, org_id).
        UniqueConstraint("id", "org_id", name="uq_tenants_id_org_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # "org" | "personal"
    org_id: Mapped[int | None] = mapped_column(ForeignKey("orgs.id"), nullable=True)
    personal_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "admin" | "member"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        # Partial unique on lower(email) so a concurrent double-insert of the same pending invite
        # gets IntegrityError. Declared here too so create_all (tests) enforces it.
        Index(
            "uq_invitations_org_email_pending",
            "org_id",
            text("lower(email)"),
            unique=True,
            postgresql_where="status = 'pending'",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id"), nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending|accepted|revoked
    invited_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), nullable=False)


class ScanResult(Base):
    __tablename__ = "scan_results"
    __table_args__ = (Index("ix_scan_results_owner_created_at", "owner", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    total_checks: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_checks: Mapped[int] = mapped_column(Integer, nullable=False)
    checks_json: Mapped[str] = mapped_column(Text, nullable=False)
    # Set for personal-endpoint scans only, to gate GET /me/analytics/history; org scans leave it null.
    scanned_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Best-effort backfill (owner -> org, else scanner's personal tenant); nullable for unmatched rows.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)


class AppConfig(Base):
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AutomationRepoSetting(Base):
    """Per-(tenant, repo, feature) opt-in switch + saved options for write automations.

    ``enabled`` defaults False; ``mode`` is feature-specific; ``extra`` holds per-feature JSON."""

    __tablename__ = "automation_repo_settings"
    __table_args__ = (Index("ix_automation_repo_settings_tenant_feature", "tenant_id", "feature"),)

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    repo: Mapped[str] = mapped_column(String, primary_key=True)
    feature: Mapped[str] = mapped_column(String, primary_key=True)
    mode: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


engine = create_engine(settings.database_url.get_secret_value())
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def set_session_user(db: Session, user_id: int) -> None:
    """Set app.user_id alone, for write paths that know the acting user but not a single tenant.

    Satisfies the RLS self-access clause (user_id match) on memberships/github_installations.
    """
    db.execute(text(f"SET app.user_id = {int(user_id)}"))


def set_session_tenant(db: Session, tenant_id: int) -> None:
    """Set app.tenant_id alone, for system paths with no acting user (e.g. the webhook receiver).

    Only sets session state for RLS; tenant_id must come from a trusted resolution
    (e.g. resolve_installation_tenant_id()).
    """
    db.execute(text(f"SET app.tenant_id = {int(tenant_id)}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        # Plain SET (see rbac) survives commit and persists on the pooled connection, so reset it
        # before returning to the pool or it leaks into the next request.
        try:
            db.rollback()
            db.execute(text("RESET app.tenant_id"))
            db.execute(text("RESET app.user_id"))
            db.commit()
            db.close()
        except Exception:
            # If the reset may not have taken, invalidate() so a possibly tenant-scoped connection
            # is discarded rather than reused.
            logger.exception("failed to reset tenant session context; invalidating connection instead of reusing it")
            db.invalidate()
