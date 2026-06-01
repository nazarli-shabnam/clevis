"""GitHub OAuth sign-in router — /auth/github/*.

  GET /auth/github/login     -> 307 redirect to GitHub's authorize page (with a signed CSRF state)
  GET /auth/github/callback  -> verify state, exchange code, fetch identity, find-or-create the
                                local user, set the httpOnly session cookie, redirect to the UI

Find-or-create policy (S1, pre-multi-tenancy): link to an existing user by GitHub id, else by
verified email (preserving that user's role); otherwise create a new user — the first-ever user
becomes the owner, mirroring the email/password setup flow.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from src.core.auth import create_access_token, set_session_cookie
from src.core.config import settings
from src.core.db import User, get_db
from src.services import github_oauth

logger = logging.getLogger(__name__)
router = APIRouter()


def _callback_url(request: Request) -> str:
    return str(request.url_for("github_callback"))


def _ui_redirect_target() -> str:
    return settings.cors_origins[0] if settings.cors_origins else "/"


def find_or_create_user(db: Session, identity: github_oauth.GitHubIdentity) -> User:
    user = db.query(User).filter(User.github_user_id == identity.github_user_id).first()
    if user is None:
        user = db.query(User).filter(User.email == identity.email).first()
    if user is not None:
        # Link / refresh the GitHub identity on the existing user; keep their role.
        user.github_user_id = identity.github_user_id
        user.github_login = identity.login
        user.avatar_url = identity.avatar_url
        if not user.name and identity.name:
            user.name = identity.name
        db.commit()
        db.refresh(user)
        return user
    is_owner = db.query(User).count() == 0
    user = User(
        email=identity.email,
        name=identity.name,
        password_hash=None,
        is_owner=is_owner,
        github_user_id=identity.github_user_id,
        github_login=identity.login,
        avatar_url=identity.avatar_url,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/login")
def github_login(request: Request):
    try:
        state = github_oauth.sign_state()
        url = github_oauth.build_authorize_url(state=state, redirect_uri=_callback_url(request))
    except github_oauth.GitHubOAuthNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return RedirectResponse(url, status_code=307)


@router.get("/callback", name="github_callback")
def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    if not code or not state or not github_oauth.verify_state(state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state or code")
    try:
        user_token = github_oauth.exchange_code_for_token(code, redirect_uri=_callback_url(request))
        identity = github_oauth.fetch_identity(user_token)
    except github_oauth.GitHubOAuthNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except github_oauth.GitHubOAuthError as exc:
        logger.warning("GitHub OAuth callback failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))
    user = find_or_create_user(db, identity)
    token = create_access_token(user.id, user.email, user.is_owner, user.name)
    response = RedirectResponse(_ui_redirect_target(), status_code=303)
    set_session_cookie(response, token)
    return response
