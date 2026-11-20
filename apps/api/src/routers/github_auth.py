"""GitHub OAuth sign-in router — /auth/github/*: login redirects to GitHub's authorize
page with a CSRF state; callback verifies state, exchanges code, and finds-or-creates
the local user (first-ever user becomes workspace admin).

Deliberately does NOT link by email match: self-registration has no email-ownership
verification, so auto-linking by email alone would let an attacker who pre-registers a
victim's email silently inherit that victim's GitHub identity. A matching email on a
different *verified* account redirects with an error instead of linking. An unverified, GitHub-less
self-registration proved nothing about the address, so GitHub's verified identity claims that row
instead (the row is replaced by a fresh account); otherwise the squatter would lock the real owner out.

A password account that wants a GitHub identity links it explicitly while signed in
(``POST /link`` starts it, the same ``/callback`` finishes it): GitHub proves who controls
the GitHub account, and the start request (authenticated with the caller's own credentials)
signs a state for that user and their ``token_version`` and binds it to the browser that started
it. Password login does not set a session cookie, so the callback cannot rely on one; the state
carries the proof instead. See ``_complete_link``.
"""

import logging
import time

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.app_config import registration_enabled
from src.core.auth import (
    SESSION_COOKIE_NAME,
    SETUP_LOCK_KEY,
    UserOut,
    _revocation_key,
    decode_access_token,
    require_auth,
    set_session_cookie,
    start_session,
)
from src.core.config import settings
from src.core.db import RevokedToken, User, get_db, set_session_user
from src.core.rate_limit import rate_limit
from src.repositories import invitation_repo, tenant_repo
from src.services import account_deletion, auth_audit, github_oauth, org_provisioning

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


def _is_unclaimed_registration(user: User) -> bool:
    """A self-registered row nobody has proven they own: unverified, no GitHub identity, not an admin."""
    return not user.email_verified and user.github_user_id is None and not user.is_workspace_admin


def find_or_create_user(db: Session, identity: github_oauth.GitHubIdentity) -> User:
    user = db.query(User).filter(User.github_user_id == identity.github_user_id).first()
    if user is not None:
        _refresh_profile(user, identity)
        db.commit()
        db.refresh(user)
        return user

    squatter = db.query(User).filter(func.lower(User.email) == identity.email.lower()).with_for_update().first()
    replaced_user_id: int | None = None
    if squatter is not None:
        if not _is_unclaimed_registration(squatter):
            raise EmailAlreadyRegistered(identity.email)
        # GitHub vouches for this address (fetch_identity only returns a verified one) and the registrant never
        # proved it, so the row is replaced, not taken over: deleting it removes its sessions and everything the
        # registrant stored in their personal tenant, and the real owner starts from a fresh account below.
        replaced_user_id = squatter.id
        set_session_user(db, squatter.id)
        try:
            account_deletion.delete_account(db, squatter)
        except account_deletion.AccountDeletionRefused:
            db.rollback()
            raise EmailAlreadyRegistered(identity.email) from None

    # Same lock as /auth/setup: serialize the first-user check-then-insert.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETUP_LOCK_KEY})
    # A concurrent callback for this same identity may have committed while we waited: it's
    # a returning user now, not a new signup, so the registration gate doesn't apply.
    user = db.query(User).filter(User.github_user_id == identity.github_user_id).first()
    if user is not None:
        _refresh_profile(user, identity)
        # The deletion above is committed here too, so it needs its audit row on this path as well.
        if replaced_user_id is not None:
            auth_audit.record(
                db, user, "auth.unverified_registration_replaced", extra={"replaced_user_id": replaced_user_id}, commit=False
            )
        db.commit()
        db.refresh(user)
        return user
    is_workspace_admin = db.query(User).count() == 0
    # A GitHub-verified email that has a pending invitation is the invited person, so a closed registration
    # does not apply to them.
    if not is_workspace_admin and not registration_enabled(db) and not invitation_repo.list_pending_for_email(db, identity.email):
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
    if replaced_user_id is not None:
        auth_audit.record(
            db, user, "auth.unverified_registration_replaced", extra={"replaced_user_id": replaced_user_id}, commit=False
        )
    db.commit()
    db.refresh(user)
    return user


def _set_state_cookie(response: Response, nonce: str) -> None:
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
    _set_state_cookie(response, nonce)
    return response


class LinkStartResponse(BaseModel):
    url: str


@router.post("/link", response_model=LinkStartResponse, dependencies=[Depends(rate_limit())])
def github_link_start(
    request: Request,
    response: Response,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Start linking a GitHub account to the signed-in user: returns the GitHub authorize URL for the browser
    to open (and sets the state cookie that binds the flow to this browser). A POST with the caller's own
    credentials, so another site cannot start it for them."""
    row = db.query(User).filter(User.id == user.id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if row.github_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This account is already linked to GitHub (@{row.github_login}).",
        )
    # The session that started the flow, by the id it carries (require_auth has already verified the token): the
    # callback refuses the link if that session was logged out meanwhile.
    presented = request.headers.get("authorization", "")
    token = presented[7:] if presented[:7].lower() == "bearer " else request.cookies.get(SESSION_COOKIE_NAME)
    claims = decode_access_token(token) if token else {}
    # The token's own version and revocation key (not the row's now), so a revoke-all that lands after
    # require_auth cannot be signed into the state as current; legacy tokens use their fingerprint key.
    try:
        state, nonce = github_oauth.sign_state(
            link_user_id=user.id,
            link_token_version=claims.get("token_version", 0),
            link_session_jti=_revocation_key(token, claims) if token else None,
            link_session_exp=claims.get("exp"),
        )
        url = github_oauth.build_authorize_url(state=state, redirect_uri=_callback_url(request))
    except github_oauth.GitHubOAuthNotConfigured:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="GitHub sign-in isn't set up on this server yet, so an account can't be linked. Ask an admin to configure it.",
        )
    _set_state_cookie(response, nonce)
    return LinkStartResponse(url=url)


def _ui_settings_redirect(query: str) -> RedirectResponse:
    base = settings.cors_origins[0] if settings.cors_origins else ""
    response = RedirectResponse(f"{base}/settings?{query}", status_code=303)
    _clear_state_cookie(response)
    return response


def _complete_link(
    request: Request, db: Session, code: str, state: str, session_cookie: str | None
) -> RedirectResponse:
    """Finish a link started by ``github_link_start``. Every failure is a redirect back to Settings with a code the
    UI explains; nothing is written unless the checks pass."""
    link = github_oauth.decode_state_link(state)
    if link is None:
        return _ui_settings_redirect("github_link_error=session")
    uid, token_version, session_jti, session_exp = link
    # The state (signed for this user, bound to this browser) is the proof that they started the flow. Password
    # login sets no session cookie, so it is not required; but if this browser is signed in as a *different*
    # account (a cookie that authenticates as someone else), the flow was not started by them.
    if session_cookie:
        try:
            current = require_auth(None, session_cookie, db)
        except HTTPException:
            current = None
        if current is not None and current.id != uid:
            return _ui_settings_redirect("github_link_error=session")
    try:
        user_token = github_oauth.exchange_code_for_token(code, redirect_uri=_callback_url(request))
        identity = github_oauth.fetch_identity(user_token)
    except github_oauth.GitHubOAuthNotConfigured:
        return _ui_settings_redirect("github_link_error=not_configured")
    except github_oauth.GitHubOAuthError as exc:
        logger.warning("GitHub link callback failed: %s", exc)
        return _ui_settings_redirect("github_link_error=oauth")

    # Locked from here to the commit, so two callbacks for one account (two tabs) are serialized: the second
    # re-reads the first's link and takes the other_account branch instead of overwriting it.
    user = db.query(User).filter(User.id == uid).with_for_update().first()
    # Signing out everywhere (token_version) or ending the session that started the flow cancels it.
    if user is None or user.token_version != token_version:
        return _ui_settings_redirect("github_link_error=session")
    # The revocation row is purged at the session's own expiry, so past it the denylist can no longer vouch.
    if session_exp is not None and time.time() >= session_exp:
        return _ui_settings_redirect("github_link_error=session")
    if session_jti and db.get(RevokedToken, session_jti) is not None:
        return _ui_settings_redirect("github_link_error=session")
    if user.github_user_id is not None and user.github_user_id != identity.github_user_id:
        # Unlinking is not offered, so a different GitHub identity cannot replace the linked one.
        return _ui_settings_redirect("github_link_error=other_account")
    taken = db.query(User).filter(User.github_user_id == identity.github_user_id, User.id != uid).first()
    if taken is not None:
        return _ui_settings_redirect("github_link_error=taken")
    user.github_user_id = identity.github_user_id
    _refresh_profile(user, identity)
    try:
        db.commit()
    except IntegrityError:
        # Another account claimed this GitHub identity between the check above and the commit.
        db.rollback()
        return _ui_settings_redirect("github_link_error=taken")
    db.refresh(user)
    org_provisioning.sync_org_admin_memberships(db, user, user_token)
    auth_audit.record(db, user, "auth.github_linked", request=request, extra={"github_login": identity.login})
    return _ui_settings_redirect("github=linked")


@router.get("/callback", name="github_callback", dependencies=[Depends(rate_limit())])
def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    cookie_nonce = request.cookies.get(github_oauth.STATE_COOKIE_NAME)
    # A sign-in state is the common case. Only a state that is not one, and is a valid link state for this browser,
    # takes the link path (the two purposes never verify as each other).
    if (
        state
        and not github_oauth.verify_state(state, cookie_nonce=cookie_nonce)
        and github_oauth.verify_state(state, cookie_nonce=cookie_nonce, purpose=github_oauth.LINK_PURPOSE)
    ):
        if not code:  # e.g. the user denied access on GitHub (error=access_denied)
            return _ui_settings_redirect("github_link_error=oauth")
        return _complete_link(request, db, code, state, session)
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
    token = start_session(db, request, user)
    auth_audit.record(db, user, "auth.login", request=request, extra={"method": "github"})
    redirect_target = f"{_ui_redirect_target()}{next_path}" if next_path else _ui_redirect_target()
    response = RedirectResponse(redirect_target, status_code=303)
    set_session_cookie(response, token)
    _clear_state_cookie(response)
    return response
