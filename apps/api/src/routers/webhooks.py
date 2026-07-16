"""GitHub App webhook receiver.

  POST /webhooks/github   verifies X-Hub-Signature-256, then handles installation
                           lifecycle events to keep github_installations in sync.
"""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import get_db
from src.repositories import audit_repo, installation_repo

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


@router.post("/webhooks/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)):
    secret = settings.github_app_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="GitHub webhook secret not configured")

    raw_body = await request.body()
    if not _verify_signature(raw_body, request.headers.get("X-Hub-Signature-256"), secret.get_secret_value()):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed webhook payload")

    event = request.headers.get("X-GitHub-Event", "")

    if event == "installation" and payload.get("action") == "deleted":
        _handle_installation_deleted(db, payload)
    # installation_repositories (repo access added/removed within an existing
    # installation) has nothing to sync yet — github_installations tracks the
    # installation itself, not per-repo access, so there's no row-level change
    # to make here today. Accepted (200) so GitHub doesn't retry.

    return {"ok": True}


def _handle_installation_deleted(db: Session, payload: dict) -> None:
    installation_id = (payload.get("installation") or {}).get("id")
    if installation_id is None:
        logger.warning("installation.deleted webhook missing installation.id")
        return
    removed = installation_repo.delete_by_installation_id(db, installation_id)
    if removed:
        audit_repo.write(
            db,
            actor="github-webhook",
            action="installation.deleted",
            target=str(installation_id),
            payload={"installation_id": installation_id, "rows_removed": removed},
        )
