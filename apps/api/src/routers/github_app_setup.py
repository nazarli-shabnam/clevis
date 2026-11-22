"""Create the GitHub App from Clevis with GitHub's manifest flow (workspace admin only).

``GET  /github-app``                    where the App's credentials come from, and what an App needs
``POST /github-app/manifest``           the manifest and where to POST it (the browser submits it to GitHub)
``POST /github-app/manifest/convert``   exchange GitHub's one-time ``code`` and store the credentials

Environment variables (``GITHUB_APP_*``) keep working and take precedence, so an instance that already has them is
told to remove them instead of silently ignoring the new App."""

import logging

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_workspace_admin
from src.core.db import GitHubAppCredentials, User, get_db
from src.repositories import audit_repo, tenant_repo
from src.routers.webhooks import _INGESTED_EVENT_TYPES
from src.services import app_permissions, github_app, github_app_credentials, github_app_manifest
from src.services.github_app_manifest import ManifestError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github-app", tags=["github-app"])


class AppStatus(BaseModel):
    source: str  # "env" | "database" | "none"
    configured: bool
    app_id: str | None = None
    slug: str | None = None
    # What an App needs, generated from the code: for an App registered by hand, set exactly these.
    permissions: dict[str, str]
    read_only_permissions: dict[str, str]
    events: list[str]
    # What the registered App itself lacks (None: not checked, see app_check_error). GitHub only offers an org
    # owner "Accept new permissions" for permissions the App requests, so the App's owner must add these first.
    app_missing_permissions: dict[str, str] | None = None
    app_owner_login: str | None = None
    app_owner_type: str | None = None
    app_check_error: str | None = None


class ManifestRequest(BaseModel):
    ui_origin: str = Field(min_length=1, max_length=300)
    organization: str | None = Field(default=None, max_length=39)
    read_only: bool = False
    name: str | None = Field(default=None, max_length=34)  # GitHub's App name limit
    replace: bool = False


class ManifestResponse(BaseModel):
    post_url: str
    manifest: dict
    state: str


class ConvertRequest(BaseModel):
    code: str = Field(min_length=1, max_length=200)
    state: str = Field(min_length=1, max_length=2000)


class ConvertResponse(BaseModel):
    slug: str
    app_id: str


def _refuse_if_env_supplies_the_app() -> None:
    if github_app_credentials.env_credentials_set():
        raise HTTPException(
            status_code=409,
            detail="The GitHub App's id or key is set through environment variables, which take precedence. "
            "Remove GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY first to use an App created from Settings.",
        )


@router.get("", response_model=AppStatus)
def app_status(_user: UserOut = Depends(require_workspace_admin)) -> AppStatus:
    status = AppStatus(
        source=github_app_credentials.source(),
        configured=github_app.credentials_configured(),
        app_id=github_app_credentials.app_id(),
        slug=github_app_credentials.slug(),
        permissions=app_permissions.manifest_permissions(False),
        read_only_permissions=app_permissions.manifest_permissions(True),
        events=sorted(_INGESTED_EVENT_TYPES),
    )
    if status.configured:
        try:
            app = github_app.get_app()
        # TypeError: an encrypted PEM (jwt loads the key without a password).
        except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError, github_app.GitHubAppNotConfigured):
            logger.warning("could not read the GitHub App's permissions from GitHub", exc_info=True)
            status.app_check_error = "Could not read the App's permissions from GitHub."
        else:
            granted = app.get("permissions")
            if isinstance(granted, dict):
                status.app_missing_permissions = app_permissions.app_permission_gaps(granted)
                status.slug = status.slug or app.get("slug")
                owner = app.get("owner") or {}
                status.app_owner_login = owner.get("login")
                status.app_owner_type = owner.get("type")
            else:
                status.app_check_error = "GitHub did not return the App's permissions."
    return status


@router.post("/manifest", response_model=ManifestResponse)
def create_manifest(
    body: ManifestRequest,
    request: Request,
    user: UserOut = Depends(require_workspace_admin),
    db: Session = Depends(get_db),
) -> ManifestResponse:
    _refuse_if_env_supplies_the_app()
    if github_app_credentials.stored() is not None and not body.replace:
        raise HTTPException(
            status_code=409,
            detail="An App was already created from Settings. Creating another replaces it, and installations "
            "of the old App stop working. Confirm to replace it.",
        )
    try:
        ui_origin = github_app_manifest.check_ui_origin(body.ui_origin)
        api_base = str(request.base_url).rstrip("/")
        name = (body.name or "").strip() or f"Clevis {ui_origin.split('//', 1)[-1]}"[:34]
        manifest = github_app_manifest.build_manifest(
            ui_origin=ui_origin, api_base=api_base, name=name, read_only=body.read_only
        )
        db_user = db.get(User, user.id)
        state = github_app_manifest.sign_state(
            user_id=user.id, token_version=db_user.token_version if db_user else 0, replace=body.replace
        )
        post_url = github_app_manifest.post_url(body.organization or None, state)
    except ManifestError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ManifestResponse(post_url=post_url, manifest=manifest, state=state)


@router.post("/manifest/convert", response_model=ConvertResponse)
def convert_manifest(
    body: ConvertRequest,
    user: UserOut = Depends(require_workspace_admin),
    db: Session = Depends(get_db),
) -> ConvertResponse:
    _refuse_if_env_supplies_the_app()
    db_user = db.get(User, user.id)
    try:
        claims = github_app_manifest.verify_state(
            body.state, user_id=user.id, token_version=db_user.token_version if db_user else 0
        )
        # One conversion at a time, from this check through the commit below: otherwise two overlapping "first App"
        # conversions both see no App, and the slower one silently overwrites the credentials of the first. A
        # transaction-level advisory lock (a row lock cannot cover a row that does not exist yet) is released by
        # that commit or by the rollback; the check reads the table itself, not the few-seconds-old credentials cache.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('github_app_credentials'))"))
        already_created = db.query(GitHubAppCredentials.id).filter(GitHubAppCredentials.id == 1).first() is not None
        if already_created and not claims.get("rep"):
            raise ManifestError("An App was already created from Settings; start again and confirm replacing it.")
        data = github_app_manifest.convert_code(body.code)
    except ManifestError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        github_app_credentials.save(
            db,
            app_id=str(data["id"]),
            slug=str(data["slug"]),
            client_id=str(data["client_id"]),
            client_secret=str(data["client_secret"]),
            webhook_secret=str(data["webhook_secret"]),
            private_key=str(data["pem"]),
            created_by_user_id=user.id,
        )
        audit_repo.write(
            db,
            user.email,
            "github_app.created",
            str(data["slug"]),
            {"app_id": str(data["id"]), "replaced": bool(claims.get("rep"))},
            tenant_id=tenant_repo.ensure_personal_tenant(db, user.id, commit=False).id,
            commit=False,
        )
        db.commit()
    except Exception:
        db.rollback()
        github_app_credentials.clear_cache()
        logger.exception("could not store the GitHub App credentials")
        raise HTTPException(status_code=500, detail="GitHub created the App but its credentials could not be stored (GitHub shows them only once). "
            "Delete the App on GitHub and start again.",
        )
    github_app_credentials.clear_cache()
    github_app.clear_cache()
    return ConvertResponse(slug=str(data["slug"]), app_id=str(data["id"]))
