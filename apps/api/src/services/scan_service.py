"""Storing a scan and alerting on a score drop, shared by the session scan routes, the API-token scan
route and the periodic scheduled-scan sweep (so all three apply exactly the same rules)."""

import logging

from sqlalchemy.orm import Session

from src.repositories import scan_results_repo
from src.services import notifications

logger = logging.getLogger(__name__)


def persist_scan(
    db: Session,
    result: dict,
    tenant_id: int | None,
    scanned_by_user_id: int | None = None,
    owner: str | None = None,
) -> None:
    scan_results_repo.insert(
        db,
        owner=owner or result["owner"],
        score=result["score"],
        total_checks=result["total_checks"],
        failed_checks=result["failed_checks"],
        checks=result["checks"],
        tenant_id=tenant_id,
        scanned_by_user_id=scanned_by_user_id,
    )


def comparable_scans(previous: dict, result: dict) -> bool:
    """Whether a score drop between two scans reflects a real change worth alerting on.

    An errored check (GitHub hiccup, missing permission) lowers the score without the org getting
    worse, and a different set of scored checks (e.g. ``score_hygiene_checks`` toggled) means the two
    scores aren't on the same basis. Rows stored before the ``scored`` stamp count as scored unless informational."""
    def scored_ids(checks: list[dict]) -> set[str]:
        return {c.get("id") for c in checks if c.get("scored", not c.get("informational"))}

    if any(c.get("status") == "error" for c in (*previous["checks"], *result["checks"])):
        return False
    return scored_ids(previous["checks"]) == scored_ids(result["checks"])


def persist_scan_and_alert(db: Session, org, result: dict, tenant_id: int, *, actor: str = "system") -> dict | None:
    """Store an org scan and alert if it dropped from the org's previous one.

    The baseline matches on tenant and case-insensitive owner, so scans typed in different casing (UI
    vs CI token) see each other. Used by both the session and API-token scan paths. `actor` is who the
    alert is audited as ("system" for a session scan, the token for a CI scan). Returns the baseline scan
    (or None) so callers can record it."""
    previous = scan_results_repo.latest_with_checks(db, org.github_login, tenant_id)
    persist_scan(db, result, tenant_id=tenant_id)
    if previous and comparable_scans(previous, result):
        notify_score_drop_best_effort(db, org, previous["score"], result["score"], actor=actor)
    return previous


def notify_score_drop_best_effort(db: Session, org, previous: int, current: int, *, actor: str = "system") -> None:
    # A chat-webhook problem must never fail or slow-fail the scan the user actually asked for.
    try:
        notifications.notify_score_drop(db, org.tenant_id, org.github_login, previous, current, actor=actor)
    except Exception:
        db.rollback()
        logger.exception("score-drop notification failed for %s", org.github_login)
