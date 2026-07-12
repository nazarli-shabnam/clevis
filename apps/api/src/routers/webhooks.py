"""GitHub App webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import get_db
from src.repositories import installation_repo

router = APIRouter()


def _verify_signature(body: bytes, signature_header: str | None) -> None:
    secret = settings.github_app_webhook_secret
    if secret is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhooks not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook signature")
    expected = hmac.new(
        secret.get_secret_value().encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature_header, f"sha256={expected}"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")


@router.post("/webhooks/github")
async def github_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    body = await request.body()
    _verify_signature(body, request.headers.get("X-Hub-Signature-256"))
    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)

    if event == "installation":
        action = payload.get("action")
        installation = payload.get("installation") or {}
        installation_id = installation.get("id")
        if installation_id is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        if action == "deleted":
            installation_repo.delete_by_installation_id(db, installation_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
