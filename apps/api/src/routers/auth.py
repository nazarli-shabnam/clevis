"""Auth router — /auth/*: setup/register/login, email verification, session management.

Login/register are rate-limited per IP (login also per email). Self-registered accounts
start unverified; setup() and GitHub-linked accounts are verified immediately since
their email is already trusted.
"""

from datetime import datetime, timedelta, timezone
import logging
import secrets

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.core.app_config import get_config
from src.core.auth import UserOut, clear_session_cookie, create_access_token, require_auth
from src.core.config import settings
from src.core.db import Org, User, get_db, set_session_user
from src.core.rate_limit import check_account_rate_limit, rate_limit
from src.repositories import invitation_repo, tenant_repo
from src.services.email import EmailNotConfigured, send_verification_email

logger = logging.getLogger(__name__)
router = APIRouter()

_MIN_PASSWORD_LEN = 12
# bcrypt's hard limit (bcrypt==4.2.1 truncates silently past this) -- must be validated
# (rejected, not truncated) before hashing, or two passwords sharing the first 72 bytes
# would hash identically.
_MAX_PASSWORD_LEN = 72
_VERIFY_TOKEN_TTL = timedelta(hours=24)


def _generate_verification_token(user: User) -> None:
    """Sets a fresh verification token/expiry on `user`. Caller must db.commit() before
    calling _send_verification_email_best_effort, so the email is only sent once the
    token is durably persisted."""
    user.email_verify_token = secrets.token_urlsafe(32)
    user.email_verify_token_expires_at = datetime.now(timezone.utc) + _VERIFY_TOKEN_TTL


def _send_verification_email_best_effort(user: User) -> None:
    """Tries to email the already-persisted verification token on `user` (see
    _generate_verification_token). Never raises -- registration/resend must succeed
    regardless of whether SMTP is configured or the send itself fails."""
    try:
        verify_url = f"{settings.cors_origins[0]}/verify-email?token={user.email_verify_token}"
        send_verification_email(user.email, verify_url)
    except EmailNotConfigured:
        logger.warning("SMTP not configured -- skipping verification email for %s", user.email)
    except Exception:
        # Also catches IndexError from an empty CORS_ORIGINS -- must never raise, or
        # registration itself would break.
        logger.exception("failed to send verification email to %s", user.email)

# Arbitrary key for pg_advisory_xact_lock serializing /auth/setup (see setup()).
_SETUP_LOCK_KEY = 727100

# Checked when no real password_hash exists, so a login for a nonexistent email pays the
# same bcrypt cost as a real one -- avoids a timing side-channel revealing registered emails.
_DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"clevis-timing-safety-dummy", bcrypt.gensalt()).decode()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str | None) -> bool:
    password_bytes = password.encode()
    # An oversized guess is always wrong (setup()/register() reject over-length passwords
    # before hashing), but still run a real bcrypt call so timing doesn't leak that fact.
    if len(password_bytes) > _MAX_PASSWORD_LEN:
        password_bytes = password_bytes[:_MAX_PASSWORD_LEN]
        bcrypt.checkpw(password_bytes, (password_hash or _DUMMY_PASSWORD_HASH).encode())
        return False
    if not password_hash:
        bcrypt.checkpw(password_bytes, _DUMMY_PASSWORD_HASH.encode())
        return False
    return bcrypt.checkpw(password_bytes, password_hash.encode())


class SetupRequest(BaseModel):
    email: EmailStr
    name: str | None = None
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str | None = None
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PendingInvitationSummary(BaseModel):
    # Excludes the invite token: email isn't verified yet at register()/login() time, so
    # this must stay informational only, never a shortcut to accepting the invitation.
    org_login: str
    expires_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    pending_invitations: list[PendingInvitationSummary] = []


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str | None
    is_workspace_admin: bool
    email_verified: bool
    created_at: datetime


class PatchMeRequest(BaseModel):
    name: str | None = None


class SetupRequiredResponse(BaseModel):
    setup_required: bool


class VerifyEmailRequest(BaseModel):
    token: str


class VerifyEmailResponse(BaseModel):
    ok: bool


class ResendVerificationResponse(BaseModel):
    ok: bool
    already_verified: bool = False


def _pending_invitations_for(db: Session, email: str) -> list[PendingInvitationSummary]:
    """Invitations sent to this email that are still pending and unexpired — surfaced
    at register/login so a user doesn't need the original invite link to discover them."""
    invitations = invitation_repo.list_pending_for_email(db, email)
    if not invitations:
        return []
    org_ids = [inv.org_id for inv in invitations]
    orgs_by_id = {org.id: org for org in db.query(Org).filter(Org.id.in_(org_ids)).all()}
    return [
        PendingInvitationSummary(org_login=orgs_by_id[inv.org_id].github_login, expires_at=inv.expires_at)
        for inv in invitations
        if inv.org_id in orgs_by_id
    ]


@router.get("/setup-required", response_model=SetupRequiredResponse)
def setup_required(db: Session = Depends(get_db)):
    """No auth required — used by the UI before any redirect decision."""
    count = db.query(User).count()
    return {"setup_required": count == 0}


@router.post("/setup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit())])
def setup(body: SetupRequest, db: Session = Depends(get_db)):
    """First-run only. Returns 409 if any user already exists."""
    # Two concurrent calls could both pass count()==0 before either commits -- no unique
    # constraint catches this. The advisory lock serializes check-then-insert.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK_KEY})
    if db.query(User).count() > 0:
        raise HTTPException(status_code=409, detail="Setup already complete")
    if len(body.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters",
        )
    if len(body.password.encode()) > _MAX_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at most {_MAX_PASSWORD_LEN} bytes",
        )
    user = User(
        email=body.email.lower(),
        name=body.name,
        password_hash=_hash_password(body.password),
        is_workspace_admin=True,
        # Trusted: only the deploying operator can run first-boot setup.
        email_verified=True,
    )
    db.add(user)
    try:
        # flush (not commit) so the personal tenant/membership land in the same
        # transaction as this user, avoiding an orphaned User with no personal tenant.
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Setup already complete") from None
    set_session_user(db, user.id)
    tenant_repo.ensure_personal_tenant(db, user.id, commit=False)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.email, user.is_workspace_admin, user.name, user.token_version)
    return {"access_token": token, "user": UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=user.is_workspace_admin)}


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(rate_limit())])
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """Creates a non-admin account. 409 if setup hasn't run yet; 403 if registration is disabled."""
    if db.query(User).count() == 0:
        raise HTTPException(status_code=409, detail="Setup must be completed before registration")
    if get_config("registration_enabled", "true") != "true":
        raise HTTPException(status_code=403, detail="Registration is disabled")
    if len(body.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at least {_MIN_PASSWORD_LEN} characters",
        )
    if len(body.password.encode()) > _MAX_PASSWORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at most {_MAX_PASSWORD_LEN} bytes",
        )
    if db.query(User).filter(func.lower(User.email) == body.email.lower()).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(
        email=body.email.lower(),
        name=body.name,
        password_hash=_hash_password(body.password),
        is_workspace_admin=False,
        email_verified=False,
    )
    db.add(user)
    try:
        # flush (not commit) so user.id is ready for the verification token below; also
        # where a concurrent /auth/register race on the same email surfaces (unique
        # constraint raises here instead of a raw 500 at commit).
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists") from None
    _generate_verification_token(user)
    # commit=False: land the personal tenant/membership in the same transaction as this user.
    set_session_user(db, user.id)
    tenant_repo.ensure_personal_tenant(db, user.id, commit=False)
    db.commit()
    db.refresh(user)
    # Only send the real email after the commit above succeeds -- see
    # _generate_verification_token's docstring for why the two are split.
    _send_verification_email_best_effort(user)
    token = create_access_token(user.id, user.email, user.is_workspace_admin, user.name, user.token_version)
    return {
        "access_token": token,
        "user": UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=user.is_workspace_admin),
        # Never populated here: email isn't verified yet, so looking this up would let an
        # attacker learn a victim's pending invites just by registering with their email.
        "pending_invitations": [],
    }


@router.post("/verify-email", response_model=VerifyEmailResponse, dependencies=[Depends(rate_limit())])
def verify_email(body: VerifyEmailRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email_verify_token == body.token).first()
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    if (
        user.email_verify_token_expires_at is None
        or user.email_verify_token_expires_at < datetime.now(timezone.utc)
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")
    user.email_verified = True
    user.email_verify_token = None
    user.email_verify_token_expires_at = None
    db.commit()
    return {"ok": True}


@router.post("/resend-verification", response_model=ResendVerificationResponse, dependencies=[Depends(rate_limit())])
def resend_verification(
    current_user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == current_user.id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.email_verified:
        return {"ok": True, "already_verified": True}
    check_account_rate_limit(f"resend-verification:{user.email.lower()}")
    _generate_verification_token(user)
    db.commit()
    _send_verification_email_best_effort(user)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    """Clear the httpOnly session cookie. Bearer-token clients also drop their local token."""
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(rate_limit())])
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """Returns a JWT on valid credentials. Always returns 401 on failure (no field leaking)."""
    # Per-account limit in addition to the per-IP one -- catches credential stuffing
    # spread across many source IPs targeting one victim.
    check_account_rate_limit(f"login:{body.email.lower()}")
    user = db.query(User).filter(func.lower(User.email) == body.email.lower()).first()
    # Called unconditionally (not short-circuited by "not user or ...") -- short-circuiting
    # would skip bcrypt for a nonexistent user, reopening the timing side-channel.
    password_ok = _verify_password(body.password, user.password_hash if user else None)
    if not user or not password_ok:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user.id, user.email, user.is_workspace_admin, user.name, user.token_version)
    return {
        "access_token": token,
        "user": UserOut(id=user.id, email=user.email, name=user.name, is_workspace_admin=user.is_workspace_admin),
        "pending_invitations": _pending_invitations_for(db, user.email),
    }


@router.get("/me", response_model=MeResponse)
def get_me(current_user: UserOut = Depends(require_auth), db: Session = Depends(get_db)):
    """Returns full profile of the authenticated user."""
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/me", response_model=MeResponse)
def patch_me(
    body: PatchMeRequest,
    current_user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Update the authenticated user's display name."""
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.name is not None:
        user.name = body.name
    db.commit()
    db.refresh(user)
    return user


@router.post("/me/revoke-sessions")
def revoke_sessions(
    current_user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Invalidate every previously issued JWT for the caller (log out everywhere)."""
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.token_version += 1
    db.commit()
    return {"ok": True}
