import json
from datetime import datetime

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from src.core.db import Membership, ScanResult


def insert(
    db: Session,
    owner: str,
    score: int,
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
        )
    )
    db.commit()


def exists_for_user(db: Session, owner: str, user_id: int) -> bool:
    return (
        db.query(ScanResult.id)
        .filter(ScanResult.owner == owner, ScanResult.scanned_by_user_id == user_id)
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
        func.lower(ScanResult.owner) == org_login.lower(),
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
        query = db.query(ScanResult).filter(ScanResult.owner == owner)
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


def latest_with_checks(db: Session, owner: str, tenant_id: int) -> dict | None:
    """Newest scan for ``owner`` within ``tenant_id``, with per-check results, or None."""
    row = (
        db.query(ScanResult)
        .filter(func.lower(ScanResult.owner) == owner.lower(), ScanResult.tenant_id == tenant_id)
        .order_by(ScanResult.created_at.desc(), ScanResult.id.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "score": row.score,
        "total_checks": row.total_checks,
        "failed_checks": row.failed_checks,
        "scanned_at": row.created_at.isoformat() if row.created_at else None,
        "checks": _parse_checks(row.checks_json),
    }


def _parse_checks(checks_json: str | None) -> list[dict]:
    """Best-effort parse of a stored ``checks_json`` blob; unparseable rows yield an empty list.

    Historical data has no shape guarantee, and one bad row must not 500 the whole export."""
    if not checks_json:
        return []
    try:
        parsed = json.loads(checks_json)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


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
        query = db.query(ScanResult).filter(ScanResult.owner == owner)
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
