"""Verify GitHub App installation metadata against client sync payloads."""

from __future__ import annotations

import httpx
from fastapi import HTTPException, status

from src.core.config import settings
from src.schemas.installation import SyncInstallationsInput
from src.services import github_app


def fetch_installation(installation_id: int) -> dict:
    """Load installation metadata from GitHub using an App JWT."""
    app_jwt = github_app.generate_app_jwt()
    url = f"{settings.github_api_base}/app/installations/{installation_id}"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=20) as client:
        resp = client.get(url, headers=headers)
    if resp.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Installation not found on GitHub")
    resp.raise_for_status()
    return resp.json()


def verify_sync_payload(payload: SyncInstallationsInput) -> None:
    """Ensure the client-supplied installation_id matches GitHub's account metadata."""
    if payload.auth_mode != "app":
        return
    if payload.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="installation_id is required when auth_mode is app",
        )
    try:
        installation = fetch_installation(payload.installation_id)
    except github_app.GitHubAppNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    account = installation.get("account") or {}
    if account.get("login") != payload.account_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="installation_id does not belong to the supplied account_login",
        )
    if account.get("type") != payload.account_type:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="installation account type does not match payload account_type",
        )
