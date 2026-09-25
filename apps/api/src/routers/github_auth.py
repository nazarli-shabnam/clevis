"""GitHub OAuth sign-in router — /auth/github/*: login redirects to GitHub's authorize
page with a CSRF state; callback verifies state, exchanges code, and finds-or-creates
the local user (first-ever user becomes workspace admin).

Deliberately does NOT link by email match: self-registration has no email-ownership
verification, so auto-linking by email alone would let an attacker who pre-registers a
victim's email silently inherit that victim's GitHub identity. A matching email on a
different account redirects with an error instead of linking.
"""

import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.app_config import get_config
from src.core.auth import SETUP_LOCK_KEY, create_access_token, set_session_cookie
from src.core.config import settings
from src.core.db import User, get_db, set_session_user
from src.core.rate_limit import rate_limit
from src.repositories import tenant_repo
from src.services import github_oauth, org_provisioning

logger = logging.getLogger(__name__)
router = APIRouter()


def _callback_url(request: Request) -> str:
    return str(request.url_for("github_callback"))


def _ui_redirect_target() -> str:
    return settings.cors_origins[0] if settings.cors_origins else "/"


def _ui_login_error_redirect(error_code: str) -> RedirectResponse:
    base = settings.cors_origins[0] if settings.cors_origins else ""
    return RedirectResponse(f"{base}/login?error={error_code}", status_code=303)


class RegistrationDisabled(Exception):
    """A new (non-first) user tried to sign up via GitHub while registration_enabled is off --
    same gate as /auth/register."""


class EmailAlreadyRegistered(Exception):
    """GitHub reported a verified email that already belongs to a different local account
    with no GitHub identity linked. See the module docstring for why we refuse to
    auto-link in this case instead of silently taking over that account."""


def _refresh_profile(user: User, identity: github_oauth.GitHubIdentity) -> None:
    """Returning GitHub user -- refresh their profile fields, keep their role."""
    user.github_login = identity.login
    user.avatar_url = identity.avatar_url
    if not user.name and identity.name:
        user.name = identity.name


def find_or_create_user(db: Session, identity: github_oauth.GitHubIdentity) -> User:
    user = db.query(User).filter(User.github_user_id == identity.github_user_id).first()
    if user is not None:
        _refresh_profile(user, identity)
        db.commit()
        db.refresh(user)
        return user

    if db.query(User).filter(func.lower(User.email) == identity.email.lower()).first() is not None:
        raise EmailAlreadyRegistered(identity.email)

    # Same lock as /auth/setup: serialize the first-user check-then-insert.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETUP_LOCK_KEY})
    is_workspace_admin = db.query(User).count() == 0
    if not is_workspace_admin and get_config("registration_enabled", "true") != "true":
        db.rollback()
        raise RegistrationDisabled(identity.email)
    user = User(
        email=identity.email.lower(),
        name=identity.name,
        password_hash=None,
        is_workspace_admin=is_workspace_admin,
        github_user_id=identity.github_user_id,
        github_login=identity.login,
        avatar_url=identity.avatar_url,
        # GitHub already vouches for this email -- fetch_identity() only returns a
        # GitHub-verified address. Same trust basis as the first-run setup admin.
        email_verified=True,
    )
    db.add(user)
    try:
        # flush (not commit): land the personal tenant/membership in the same transaction
        # as this user, then commit once, avoiding an orphaned User with no personal tenant.
        db.flush()
    except IntegrityError:
        # Two concurrent requests both passed the checks above before either flushed.
        # Two different causes, two different recoveries:
        db.rollback()
        # (1) Another OAuth callback for this same GitHub identity won the race -- recover
        # gracefully by re-querying the winner's now-committed row instead of 409ing.
        user = db.query(User).filter(User.github_user_id == identity.github_user_id).first()
        if user is not None:
            _refresh_profile(user, identity)
            db.commit()
            db.refresh(user)
            return user
        # (2) Not a github_user_id collision, so the violation is on the email index --
        # some other account grabbed this email in the gap. Same rule as the check above:
        # refuse to link, redirect with an explanation instead of a raw 500.
        if db.query(User).filter(func.lower(User.email) == identity.email.lower()).first() is not None:
            raise EmailAlreadyRegistered(identity.email) from None
        raise
    set_session_user(db, user.id)
    tenant_repo.ensure_personal_tenant(db, user.id, commit=False)
    db.commit()
    db.refresh(user)
    return user


def _clear_state_cookie(response: Response) -> None:
    response.delete_cookie(key=github_oauth.STATE_COOKIE_NAME, domain=settings.session_cookie_domain, path="/")


@router.get("/login")
def github_login(request: Request, next: str | None = None):
    try:
        state, nonce = github_oauth.sign_state(next=next)
        url = github_oauth.build_authorize_url(state=state, redirect_uri=_callback_url(request))
    except github_oauth.GitHubOAuthNotConfigured:
        return _ui_login_error_redirect("github_not_configured")
    response = RedirectResponse(url, status_code=307)
    # Binds `state` to this browser -- must be SameSite=Lax (not Strict) so it's still sent
    # on the top-level GET redirect back from github.com.
    response.set_cookie(
        key=github_oauth.STATE_COOKIE_NAME,
        value=nonce,
        max_age=github_oauth.STATE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        domain=settings.session_cookie_domain,
        path="/",
    )
    return response


@router.get("/callback", name="github_callback", dependencies=[Depends(rate_limit())])
def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    cookie_nonce = request.cookies.get(github_oauth.STATE_COOKIE_NAME)
    if not code or not state or not github_oauth.verify_state(state, cookie_nonce=cookie_nonce):
        error_response = _ui_login_error_redirect("github_invalid_state")
        _clear_state_cookie(error_response)
        return error_response
    next_path = github_oauth.decode_state_next(state)
    try:
        user_token = github_oauth.exchange_code_for_token(code, redirect_uri=_callback_url(request))
        identity = github_oauth.fetch_identity(user_token)
    except github_oauth.GitHubOAuthNotConfigured:
        error_response = _ui_login_error_redirect("github_not_configured")
        _clear_state_cookie(error_response)
        return error_response
    except github_oauth.GitHubOAuthError as exc:
        logger.warning("GitHub OAuth callback failed: %s", exc)
        error_response = _ui_login_error_redirect("github_oauth_failed")
        _clear_state_cookie(error_response)
        return error_response
    try:
        user = find_or_create_user(db, identity)
    except EmailAlreadyRegistered:
        error_response = _ui_login_error_redirect("github_email_registered")
        _clear_state_cookie(error_response)
        return error_response
    except RegistrationDisabled:
        error_response = _ui_login_error_redirect("github_registration_disabled")
        _clear_state_cookie(error_response)
        return error_response
    org_provisioning.sync_org_admin_memberships(db, user, user_token)
    token = create_access_token(user.id, user.email, user.is_workspace_admin, user.name, user.token_version)
    redirect_target = f"{_ui_redirect_target()}{next_path}" if next_path else _ui_redirect_target()
    response = RedirectResponse(redirect_target, status_code=303)
    set_session_cookie(response, token)
    _clear_state_cookie(response)
    return response
