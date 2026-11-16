"""Worker-only route: a fresh GitHub installation token for a job that is running right now.

The API mints one installation token when it enqueues a job and stores it, encrypted, in the payload. A token
lives 60 minutes, but queue wait, run time and retry backoff are not bounded by that, so a long or retried job
could outlive it. The worker therefore asks here for a new token on every attempt. Minting stays in the API on
purpose: the worker has no GitHub App private key (a compromised worker would otherwise be able to mint
write-capable tokens for every tenant's installation); it holds only ``WORKER_API_TOKEN``, which can fetch a
token solely for the installation of a job that is in progress.

Disabled (404) unless ``WORKER_API_TOKEN`` is configured. Without it, or when this answers anything but a
token, the worker keeps using the token from the job payload, exactly as before."""

import hmac
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.db import GitHubInstallation, Job, get_db, set_session_tenant
from src.services import github_app

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])

# The payload field naming the GitHub account each job type works on.
_ACCOUNT_FIELD = {
    "github.clear_actions_cache": "owner",
    "github.backfill_repo_events": "account_login",
    "github.backfill_security_alerts": "account_login",
    "github.reconcile_org_membership": "org_login",
}


def _require_worker(authorization: str | None) -> None:
    expected = settings.worker_api_token.get_secret_value() if settings.worker_api_token else ""
    if not expected:  # unset or empty (compose passes "" when the variable is not set): the route is off
        raise HTTPException(status_code=404, detail="Not found")
    presented = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid worker credential")


@router.post("/jobs/{job_id}/installation-token")
def job_installation_token(
    job_id: int,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    _require_worker(authorization)

    job = db.get(Job, job_id)
    field = _ACCOUNT_FIELD.get(job.job_type) if job else None
    # Only a job that is being worked on, for a known tenant, and of a type that talks to GitHub.
    if job is None or field is None or job.status != "processing" or job.tenant_id is None:
        raise HTTPException(status_code=404, detail="No running job to issue a token for")
    try:
        login = str(json.loads(job.payload).get(field) or "")
    except ValueError:
        login = ""
    if not login:
        raise HTTPException(status_code=404, detail="No running job to issue a token for")

    set_session_tenant(db, job.tenant_id)
    installation = (
        db.query(GitHubInstallation)
        .filter(
            GitHubInstallation.tenant_id == job.tenant_id,
            func.lower(GitHubInstallation.account_login) == login.lower(),
            GitHubInstallation.installation_id.is_not(None),
        )
        .first()
    )
    if installation is None:
        # A PAT-backed job: nothing to refresh, the worker keeps the payload token.
        raise HTTPException(status_code=404, detail="No installation for this job")
    try:
        token = github_app.get_installation_token(installation.installation_id, github_app.JOB_TOKEN_MIN_TTL_SECONDS)
    except Exception:
        logger.exception("could not mint a fresh token for job %s", job_id)
        raise HTTPException(status_code=502, detail="Could not mint an installation token")
    return {"token": token}
