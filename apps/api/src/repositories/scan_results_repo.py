import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import ScanResult


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


def list_recent(
    db: Session, owner: str, limit: int = 30, scanned_by_user_id: int | None = None
) -> list[dict]:
    """Newest-first scan summaries for ``owner``.

    ``scanned_by_user_id`` restricts to that user's own scans (personal endpoint BYO-PAT history)."""
    query = db.query(ScanResult).filter(ScanResult.owner == owner)
    if scanned_by_user_id is not None:
        query = query.filter(ScanResult.scanned_by_user_id == scanned_by_user_id)
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
) -> list[dict]:
    """Scan history with full per-check breakdown for a compliance export, newest first.

    Accepts an optional ``[since, until]`` window; ``scanned_by_user_id`` restricts to that user's
    own scans. Fetches one extra row beyond ``limit`` so the caller can detect truncation.
    """
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
