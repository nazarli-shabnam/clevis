import json
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from src.core.db import Membership, ScanResult


def insert(
    db: Session,
    owner: str,
    score: int | None,
    total_checks: int,
    failed_checks: int,
    checks: list[dict],
    tenant_id: int | None = None,
    scanned_by_user_id: int | None = None,
) -> None:
    # scan_results RLS WITH CHECK is strict equality on app.tenant_id, which personal routes never
    # set. SET LOCAL to the value being written is safe (see audit_repo.write).
    if tenant_id is not None:
        db.execute(text(f"SET LOCAL app.tenant_id = {int(tenant_id)}"))
    db.add(
        ScanResult(
            owner=owner,
            score=score,
            total_checks=total_checks,
            failed_checks=failed_checks,
            checks_json=json.dumps(checks),
            tenant_id=tenant_id,
            scanned_by_user_id=scanned_by_user_id,
            # Stamped here, not by the column's now() default: that is the *transaction* start, and a
            # scan's transaction opens before its GitHub calls, so a long scan would be dated as
            # starting, not finishing (which also eats the API-token scan reuse window).
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _owner_is(owner: str):
    """Owner logins compare case-insensitively, like GitHub's. A member's scan is stored under the
    org's canonical login whatever casing they typed, so exact matching would lose their rows."""
    return func.lower(ScanResult.owner) == owner.lower()


def exists_for_user(db: Session, owner: str, user_id: int) -> bool:
    return (
        db.query(ScanResult.id)
        .filter(_owner_is(owner), ScanResult.scanned_by_user_id == user_id)
        .first()
        is not None
    )


def org_scope_filter(org_login: str, org_tenant_id: int):
    """SQL filter for the scans that belong to a Clevis org: ``org_login``'s scans that are stored
    under the org's tenant, or that were run by a current member of it.

    ``scan_results`` reads are ``USING (true)`` (migration 0033), so neither RLS nor the owner string
    alone stops another tenant's row with the same ``owner``: ``POST /me/analytics/overview`` accepts
    any owner login the caller has a token for. The member clause covers scans a member ran through
    that personal route before they were stored under the org tenant; rows from non-members never
    match. The membership subquery needs the org's RLS tenant context to see every member.
    """
    member_ids = select(Membership.user_id).where(Membership.tenant_id == org_tenant_id)
    return and_(
        _owner_is(org_login),
        or_(ScanResult.tenant_id == org_tenant_id, ScanResult.scanned_by_user_id.in_(member_ids)),
    )


def list_recent(
    db: Session,
    owner: str,
    limit: int = 30,
    scanned_by_user_id: int | None = None,
    tenant_id: int | None = None,
    org_tenant_id: int | None = None,
) -> list[dict]:
    """Newest-first scan summaries for ``owner``.

    ``org_tenant_id`` returns the org's scans (see ``org_scope_filter``); ``scanned_by_user_id``
    restricts to that user's own scans (personal endpoint BYO-PAT history); ``tenant_id`` requires an
    exact tenant match."""
    if org_tenant_id is not None:
        query = db.query(ScanResult).filter(org_scope_filter(owner, org_tenant_id))
    else:
        query = db.query(ScanResult).filter(_owner_is(owner))
    if scanned_by_user_id is not None:
        query = query.filter(ScanResult.scanned_by_user_id == scanned_by_user_id)
    if tenant_id is not None:
        # Explicit, not left to RLS: scan_results reads are USING (true), and with RLS inert (default
        # superuser deployment) another tenant's scan of the same owner login would otherwise count
        # as "the previous scan".
        query = query.filter(ScanResult.tenant_id == tenant_id)
    rows = (
        query.order_by(ScanResult.created_at.desc(), ScanResult.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "owner": r.owner,
            "score": r.score,
            "total_checks": r.total_checks,
            "failed_checks": r.failed_checks,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


def _with_checks(row: ScanResult) -> dict:
    return {
        "owner": row.owner,
        "score": row.score,
        "total_checks": row.total_checks,
        "failed_checks": row.failed_checks,
        "scanned_at": row.created_at.isoformat() if row.created_at else None,
        "checks": _parse_checks(row.checks_json),
        "checks_parseable": _parse_checks_strict(row.checks_json) is not None,
    }


def has_errored_check(checks: list[dict]) -> bool:
    return any(c.get("status") == "error" for c in checks)


def is_clean_baseline(scan: dict) -> bool:
    """A ``_with_checks`` scan whose stored blob parsed to a list (history has no shape guarantee: invalid or
    non-list JSON is no baseline), of dicts only, with nothing errored."""
    checks = scan["checks"]
    return scan["checks_parseable"] and all(isinstance(c, dict) for c in checks) and not has_errored_check(checks)


def _newest_with_checks(db: Session, *criteria) -> dict | None:
    row = db.query(ScanResult).filter(*criteria).order_by(ScanResult.created_at.desc(), ScanResult.id.desc()).first()
    return _with_checks(row) if row is not None else None


def latest_with_checks(db: Session, owner: str, tenant_id: int) -> dict | None:
    """Newest scan for ``owner`` within ``tenant_id``, with per-check results, or None."""
    return _newest_with_checks(db, func.lower(ScanResult.owner) == owner.lower(), ScanResult.tenant_id == tenant_id)


def latest_without_errors(db: Session, owner: str, tenant_id: int, scan_limit: int = 10) -> dict | None:
    """Newest of the last ``scan_limit`` scans for ``owner`` within ``tenant_id`` that has no errored check.

    An errored scan's score is depressed by a GitHub hiccup, not by the org getting worse, so it is no
    baseline for a score-drop comparison. ``checks_json`` is plain text, so this filters in Python.
    # ponytail: bounded lookback, widen scan_limit if orgs routinely see >10 errored scans in a row"""
    rows = (
        db.query(ScanResult)
        .filter(func.lower(ScanResult.owner) == owner.lower(), ScanResult.tenant_id == tenant_id)
        .order_by(ScanResult.created_at.desc(), ScanResult.id.desc())
        .limit(scan_limit)
    )
    return next((scan for scan in map(_with_checks, rows) if is_clean_baseline(scan)), None)


def latest_for_org(db: Session, owner: str, org_tenant_id: int) -> dict | None:
    """Newest scan of a Clevis org with per-check results, or None. Same scope as the org's history
    (``org_scope_filter``): rows under the org's tenant, plus scans a current member ran themselves."""
    return _newest_with_checks(db, org_scope_filter(owner, org_tenant_id))


def _parse_checks_strict(checks_json: str | None) -> list | None:
    """The stored blob as a list, or None when it is empty, invalid JSON, or not a list."""
    if not checks_json:
        return None
    try:
        parsed = json.loads(checks_json)
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def _parse_checks(checks_json: str | None) -> list[dict]:
    """Best-effort parse of a stored ``checks_json`` blob; unparseable rows yield an empty list.

    Historical data has no shape guarantee, and one bad row must not 500 the whole export."""
    parsed = _parse_checks_strict(checks_json)
    return [] if parsed is None else parsed


def list_for_export(
    db: Session,
    owner: str,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 500,
    scanned_by_user_id: int | None = None,
    org_tenant_id: int | None = None,
) -> list[dict]:
    """Scan history with full per-check breakdown for a compliance export, newest first.

    Accepts an optional ``[since, until]`` window; ``org_tenant_id`` returns the org's scans (see
    ``org_scope_filter``); ``scanned_by_user_id`` restricts to that user's own scans. Fetches one
    extra row beyond ``limit`` so the caller can detect truncation.
    """
    if org_tenant_id is not None:
        query = db.query(ScanResult).filter(org_scope_filter(owner, org_tenant_id))
    else:
        query = db.query(ScanResult).filter(_owner_is(owner))
    if since is not None:
        query = query.filter(ScanResult.created_at >= since)
    if until is not None:
        query = query.filter(ScanResult.created_at <= until)
    if scanned_by_user_id is not None:
        query = query.filter(ScanResult.scanned_by_user_id == scanned_by_user_id)
    rows = query.order_by(ScanResult.created_at.desc(), ScanResult.id.desc()).limit(limit + 1).all()
    return [
        {
            "id": r.id,
            "owner": r.owner,
            "score": r.score,
            "total_checks": r.total_checks,
            "failed_checks": r.failed_checks,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "checks": _parse_checks(r.checks_json),
        }
        for r in rows
    ]
