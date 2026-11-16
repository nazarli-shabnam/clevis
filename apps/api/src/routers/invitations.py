"""Org invitation router: admin create (single or bulk) / list / revoke / regenerate pending invites
(shareable link; an email goes out only on an explicit resend or when create asks for it with ``send_email``), unauthenticated preview by token,
and accept -- which requires the caller's account email to case-insensitively match the invite
AND be verified.
"""

import logging
import smtplib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_auth
from src.core.config import settings
from src.core.db import Org, User, get_db
from src.core.rate_limit import check_account_rate_limit, rate_limit
from src.core.rbac import OrgContext, require_org_role
from src.repositories import audit_repo, invitation_repo, org_membership_repo
from src.schemas.invitation import (
    InvitationAcceptResponse,
    InvitationBulkCreate,
    InvitationBulkItem,
    InvitationBulkResponse,
    InvitationCreate,
    InvitationCreateResponse,
    InvitationOut,
    InvitationPreview,
    MyInvitation,
)
from src.services import email

router = APIRouter()
logger = logging.getLogger(__name__)
_EMAIL = TypeAdapter(EmailStr)


def _ui_base() -> str:
    return settings.cors_origins[0] if settings.cors_origins else ""


def _invite_link(invitation) -> str:
    return f"{_ui_base()}/invite/{invitation.token}"


def _masked_email(address: str) -> str:
    """"alice@acme.com" -> "a***@acme.com": enough for the invitee to recognise which of their addresses the
    invitation is for, without handing the full address to whoever holds a (shareable) invite link."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


_SMTP_UNCONFIGURED = (
    "Email isn't set up on this instance (SMTP_HOST and SMTP_FROM). Invite without emailing and share the link yourself."
)


def _require_smtp_for(send_email: bool) -> None:
    # Checked before anything is created, so a request that can't deliver doesn't leave invitations behind.
    if send_email and not email.is_configured():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_SMTP_UNCONFIGURED)


# Invitation emails go to addresses the admin types, so an org's volume is capped (per process, like every
# limiter here): requests-per-IP alone would let one bulk call mail 50 strangers over and over.
_MAX_INVITE_EMAILS_PER_HOUR = 100


def _email_invitation(address: str, org_login: str, inviter: str, link: str, tenant_id: int) -> tuple[str, bool]:
    """Email the link after the invitation is committed, so a slow or failing SMTP server never holds
    a transaction open or loses the invitation. Returns (status, server_down): the link is returned to
    the caller either way, and ``server_down`` is False for a single refused recipient. Over the org's
    hourly cap nothing is sent and the status is "limited" (never an error: the invitation already exists)."""
    try:
        check_account_rate_limit(f"invite-email:{tenant_id}", max_requests=_MAX_INVITE_EMAILS_PER_HOUR, window_seconds=3600)
    except HTTPException:
        logger.warning("Invitation email cap reached for %s", org_login)
        return "limited", False
    try:
        email.send_invitation_email(address, org_login, inviter, link)
    except smtplib.SMTPRecipientsRefused:
        logger.warning("The mail server refused an invitation recipient for %s", org_login, exc_info=True)
        return "failed", False
    except OSError:  # smtplib.SMTPException and ssl.SSLError are OSErrors
        logger.warning("Failed to email an invitation for %s", org_login, exc_info=True)
        return "failed", True
    except Exception:
        # The invitation is already committed and its link must still reach the caller, whatever went wrong here.
        logger.exception("Unexpected error emailing an invitation for %s", org_login)
        return "failed", False
    return "sent", False


# A bulk request has to answer before the browser gives up on it (15 s), and 50 sequential sends at ~0.5 s each
# would not. Sends run a few at a time and stop being waited for after this budget; whatever has not finished
# is reported, with its link, instead of being lost in a timeout.
_EMAIL_PARALLEL_SENDS = 5
_EMAIL_BUDGET_SECONDS = 8.0


def _deliver_bulk(items: list, *, org_login: str, tenant_id: int, inviter: str) -> None:
    """Email each created invitation and record the outcome on the item (``email_status``). The first goes alone,
    so a dead mail server is discovered once instead of by every worker at the same time, and then the remaining
    ones are skipped. Everything, the first send included, shares one ``_EMAIL_BUDGET_SECONDS`` deadline: a send
    still in flight at it is reported ``unconfirmed`` (it may still complete) and one that never started
    ``limited`` (not sent)."""
    if not items:
        return
    deadline = time.monotonic() + _EMAIL_BUDGET_SECONDS
    down = threading.Event()

    def send(item):
        # Once any send finds the server down, queued ones neither take an hourly-cap slot nor try SMTP; nor
        # does one that only gets a thread after the budget ran out (it is reported as not sent).
        if down.is_set():
            return "failed", True
        if time.monotonic() >= deadline:
            return "limited", False
        result = _email_invitation(item.email, org_login, inviter, item.invite_link, tenant_id)
        if result[1]:
            down.set()
        return result

    first, rest = items[0], items[1:]
    # Not waited for on exit: a send already running finishes (or times out at the SMTP socket) in its own thread.
    pool = ThreadPoolExecutor(max_workers=_EMAIL_PARALLEL_SENDS)
    try:
        first_future = pool.submit(send, first)
        done, _ = wait([first_future], timeout=max(0.0, deadline - time.monotonic()))
        if not done:
            first.email_status = "unconfirmed"
            for item in rest:
                item.email_status = "limited"
            return
        first.email_status, server_down = first_future.result()
        if server_down:
            for item in rest:
                item.email_status = "failed"
            return
        futures = {pool.submit(send, item): item for item in rest}
        done, _ = wait(futures, timeout=max(0.0, deadline - time.monotonic()))
        for future, item in futures.items():
            if future in done:
                item.email_status = future.result()[0]
            else:
                item.email_status = "limited" if future.cancel() else "unconfirmed"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _is_expired(invitation) -> bool:
    return invitation.status == "pending" and invitation.expires_at <= datetime.now(timezone.utc)


def _effective_status(invitation) -> str:
    return "expired" if _is_expired(invitation) else invitation.status


def _expire(db: Session, invitation) -> None:
    invitation.status = "expired"
    db.commit()
    db.refresh(invitation)


@router.post(
    "/orgs/{org_login}/invitations",
    response_model=InvitationCreateResponse,
    dependencies=[Depends(rate_limit())],
)
def create_invitation(
    body: InvitationCreate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    _require_smtp_for(body.send_email)
    # Plain values, read before the commit below expires the ORM org: reading it afterwards would reload it and
    # check a connection out for the whole SMTP exchange.
    org_id, org_login, tenant_id = ctx.org.id, ctx.org.github_login, ctx.org.tenant_id
    existing = invitation_repo.get_pending_for_org_and_email(db, org_id=org_id, email=body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A pending invitation already exists for {body.email} in this organization",
        )
    # The invitation and its audit row commit together (as in the bulk route): a failure between two
    # commits would leave a live invite link with no audit trail.
    try:
        invitation = invitation_repo.create(
            db, org_id=org_id, email=body.email, invited_by_user_id=user.id, commit=False
        )
        audit_repo.write(
            db, user.email, "invitation.create", org_login,
            {"email": body.email, **({"email_requested": True} if body.send_email else {})},
            tenant_id=tenant_id, commit=False,
        )
        # Built before the commit, which expires the row.
        link = _invite_link(invitation)
        db.commit()
    except invitation_repo.DuplicatePendingInvitation as exc:
        # A concurrent request won the race between the pre-check above and its own
        # insert; the DB constraint rejected this one.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    email_status = (
        _email_invitation(body.email, org_login, user.email, link, tenant_id)[0]
        if body.send_email
        else None
    )
    return {"invitation": invitation, "invite_link": link, "email_status": email_status}


@router.post(
    "/orgs/{org_login}/invitations/bulk",
    response_model=InvitationBulkResponse,
    dependencies=[Depends(rate_limit())],
)
def create_invitations_bulk(
    body: InvitationBulkCreate,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Invite several addresses at once. Each distinct address gets its own result, so one bad or
    duplicate address never blocks the rest; an address repeated in the request (compared
    case-insensitively) is reported once, for its first occurrence. With ``send_email`` each created
    invitation is also emailed and reports its own delivery result."""
    _require_smtp_for(body.send_email)
    # Plain values: every commit below expires the ORM org, and each read after it would reload it.
    org_id, org_login, tenant_id = ctx.org.id, ctx.org.github_login, ctx.org.tenant_id
    results: list[InvitationBulkItem] = []
    seen: set[str] = set()
    created: list[InvitationBulkItem] = []
    for raw in body.emails:
        raw = raw.strip()
        try:
            address = _EMAIL.validate_python(raw)
            valid = True
        except ValidationError:
            address, valid = raw, False
        # Valid addresses are keyed by their normalised form, invalid ones by what was typed.
        if address.lower() in seen:
            continue
        seen.add(address.lower())
        if not valid:
            results.append(InvitationBulkItem(email=raw, status="invalid"))
            continue
        # One transaction per address: the invitation and its audit row land together or not at all,
        # so a failure never leaves an invitation whose link and audit trail are both missing.
        try:
            if invitation_repo.get_pending_for_org_and_email(db, org_id=org_id, email=address) is not None:
                results.append(InvitationBulkItem(email=address, status="already_pending"))
                continue
            invitation = invitation_repo.create(
                db, org_id=org_id, email=address, invited_by_user_id=user.id, commit=False
            )
            audit_repo.write(
                db, user.email, "invitation.create", org_login,
                {"email": address, **({"email_requested": True} if body.send_email else {})},
                tenant_id=tenant_id, commit=False,
            )
            # Built before the commit: commit expires the row, and a read after it could fail
            # outside this try and lose the links of every address already committed.
            item = InvitationBulkItem(
                email=address, status="created", invitation=invitation, invite_link=_invite_link(invitation)
            )
            db.commit()
        except invitation_repo.DuplicatePendingInvitation:
            results.append(InvitationBulkItem(email=address, status="already_pending"))
            continue
        except SQLAlchemyError:
            logger.warning("Bulk invitation for org %s failed", org_login, exc_info=True)
            db.rollback()
            results.append(InvitationBulkItem(email=address, status="failed"))
            continue
        created.append(item)
        results.append(item)
    if body.send_email:
        db.rollback()  # end any read transaction the last lookup left open: SMTP must not hold a pooled connection
        _deliver_bulk(created, org_login=org_login, tenant_id=tenant_id, inviter=user.email)
    return {"results": results}


@router.get("/orgs/{org_login}/invitations", response_model=list[InvitationOut])
def list_invitations(
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    db: Session = Depends(get_db),
):
    invitations = invitation_repo.list_for_org(db, org_id=ctx.org.id)
    return [
        InvitationOut(
            id=inv.id,
            org_id=inv.org_id,
            email=inv.email,
            status=_effective_status(inv),
            created_at=inv.created_at,
            accepted_at=inv.accepted_at,
            expires_at=inv.expires_at,
        )
        for inv in invitations
    ]


@router.post("/orgs/{org_login}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
def revoke_invitation(
    invitation_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    # Locked so a concurrent accept can't pass its pending check and then overwrite this revoke.
    invitation = invitation_repo.get_by_id_and_org(
        db, invitation_id=invitation_id, org_id=ctx.org.id, for_update=True
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if invitation.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invitation is not pending")
    invitation.status = "revoked"
    # The revoke and its audit row commit together: a failure between two commits would leave a revoked
    # invitation with no audit trail.
    audit_repo.write(
        db, user.email, "invitation.revoke", ctx.org.github_login, {"email": invitation.email},
        tenant_id=ctx.org.tenant_id, commit=False,
    )
    db.commit()
    db.refresh(invitation)
    return invitation


def _reissue(db: Session, ctx: OrgContext, invitation_id: int):
    """Rotate the token of a pending or expired invitation of this org (accepted and revoked ones
    are final). Returns the invitation, now pending with a new link.

    The rotation is flushed, not committed: the caller's audit_repo.write (default commit=True)
    commits it together with its audit row. The row is locked from the status check on, so a
    concurrent create() can't expire it between the check and the write."""
    invitation = invitation_repo.get_by_id_and_org(
        db, invitation_id=invitation_id, org_id=ctx.org.id, for_update=True
    )
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if _effective_status(invitation) not in ("pending", "expired"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only pending or expired invitations can be reissued"
        )
    try:
        return invitation_repo.reissue(db, invitation)
    except invitation_repo.DuplicatePendingInvitation as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/orgs/{org_login}/invitations/{invitation_id}/regenerate",
    response_model=InvitationCreateResponse,
    dependencies=[Depends(rate_limit())],
)
def regenerate_invitation(
    invitation_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """New link and a fresh 7 days for an existing invitation; the previous link stops working."""
    invitation = _reissue(db, ctx, invitation_id)
    audit_repo.write(
        db, user.email, "invitation.regenerate", ctx.org.github_login, {"email": invitation.email},
        tenant_id=ctx.org.tenant_id,
    )
    return {"invitation": invitation, "invite_link": _invite_link(invitation)}


@router.post(
    "/orgs/{org_login}/invitations/{invitation_id}/resend",
    response_model=InvitationOut,
    dependencies=[Depends(rate_limit())],
)
def resend_invitation(
    invitation_id: int,
    ctx: OrgContext = Depends(require_org_role(min_role="admin")),
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """Email a fresh link to the invitee. Needs SMTP; the previous link stops working."""
    # Checked first so an instance without SMTP doesn't rotate (and so lose) a link for nothing.
    if not email.is_configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email isn't set up on this instance (SMTP_HOST and SMTP_FROM). Use Regenerate link and share it yourself.",
        )
    invitation = _reissue(db, ctx, invitation_id)
    audit_repo.write(
        db, user.email, "invitation.resend", ctx.org.github_login, {"email": invitation.email},
        tenant_id=ctx.org.tenant_id,
    )
    try:
        email.send_invitation_email(invitation.email, ctx.org.github_login, user.email, _invite_link(invitation))
    except OSError:  # smtplib.SMTPException and ssl.SSLError are OSErrors
        logger.warning("Failed to email invitation %s", invitation.id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The email couldn't be sent. The previous link no longer works; use Regenerate link to get a new one.",
        )
    return invitation


@router.get("/invitations/mine", response_model=list[MyInvitation])
def my_invitations(user: UserOut = Depends(require_auth), db: Session = Depends(get_db)):
    """Pending invitations for the caller's own address, with their accept tokens, so an invitee who lost the link
    (or only just verified their email) can still accept. Empty until the email is verified: the mailbox is
    what entitles someone to the token. Declared before ``/invitations/{token}`` so ``mine`` is not read as one."""
    db_user = db.query(User).filter(User.id == user.id).first()
    if db_user is None or not db_user.email_verified:
        return []
    invitations = invitation_repo.list_pending_for_email(db, db_user.email)
    orgs = {o.id: o for o in db.query(Org).filter(Org.id.in_([i.org_id for i in invitations])).all()} if invitations else {}
    return [
        MyInvitation(org_login=orgs[i.org_id].github_login, token=i.token, expires_at=i.expires_at)
        for i in invitations
        if i.org_id in orgs
    ]


@router.get("/invitations/{token}", response_model=InvitationPreview)
def preview_invitation(token: str, db: Session = Depends(get_db)):
    invitation = invitation_repo.get_by_token(db, token)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if _is_expired(invitation):
        _expire(db, invitation)
    org = db.query(Org).filter(Org.id == invitation.org_id).first()
    return {"org_login": org.github_login, "status": invitation.status}


@router.post("/invitations/{token}/accept", response_model=InvitationAcceptResponse)
def accept_invitation(
    token: str,
    user: UserOut = Depends(require_auth),
    db: Session = Depends(get_db),
):
    # Row lock: a concurrent revoke either commits first (we then see "revoked" and refuse) or
    # waits for this transaction, which creates the membership and flips the status atomically.
    invitation = invitation_repo.get_by_token(db, token, for_update=True)
    if invitation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if _is_expired(invitation):
        _expire(db, invitation)
    if invitation.status == "expired":
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invitation has expired")
    if invitation.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invitation is no longer pending")
    if invitation.email.lower() != user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This invitation was sent to {_masked_email(invitation.email)}, but you are signed in as "
                f"{user.email}. Sign in with the invited address to accept it."
            ),
        )
    # Email match alone isn't proof of ownership for a self-registered account.
    # Accounts created by GitHub sign-in and the setup admin are verified immediately;
    # self-registered accounts must click the emailed verification link first.
    db_user = db.query(User).filter(User.id == user.id).first()
    if db_user is None or not db_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verify your email before accepting this invitation",
        )

    # An existing membership (e.g. a GitHub-verified admin) is kept as-is: accepting a member
    # invite must never demote it.
    membership = org_membership_repo.create_if_missing(
        db, org_id=invitation.org_id, user_id=user.id, role="member", source="invite", commit=False
    )
    role = membership.role
    invitation.status = "accepted"
    invitation.accepted_at = datetime.now(timezone.utc)

    org = db.query(Org).filter(Org.id == invitation.org_id).first()
    org_login = org.github_login  # read before the commit expires the row
    # The membership, the accepted status and the audit row commit together, so a failed audit write can't
    # leave a new member that nothing retries.
    audit_repo.write(
        db, user.email, "invitation.accept", org_login, {"role": role},
        tenant_id=org.tenant_id, commit=False,
    )
    db.commit()
    return {"org_login": org_login, "role": role}
