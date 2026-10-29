"""Outbound chat/webhook alerts (Slack, Teams, generic signed webhook).

Destination URLs are user-supplied and the server calls them, so every send re-validates
that the URL is https and resolves only to public addresses (no loopback, private, link-local
or otherwise reserved ranges) and never follows redirects. Failures are reported as a short
reason that never includes the URL, since a Slack/Teams webhook URL is itself a secret.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from src.core._crypto import decrypt_job_token
from src.core.config import settings
from src.core.db import NotificationDestination
from src.services import email
from src.repositories import audit_repo, notification_repo

logger = logging.getLogger(__name__)

KINDS = ("slack", "teams", "generic", "email")
EVENT_SCORE_DROP = "score_drop"
EVENT_WAIVER_EXPIRED = "waiver_expired"
EVENTS = (EVENT_SCORE_DROP, EVENT_WAIVER_EXPIRED)
_TIMEOUT_SECONDS = 5
# Destinations are messaged in parallel so one slow endpoint can't stall a scan request for
# (destinations x timeout).
_MAX_PARALLEL_SENDS = 5
# A destination is alerted at most once per window, so concurrent scans or a flapping score can't spam it.
ALERT_COOLDOWN = timedelta(hours=1)


class UnsafeDestinationURL(ValueError):
    pass


def _resolve_public_ip(url: str) -> str:
    """Return a public IP the https `url` resolves to, or raise UnsafeDestinationURL.

    Every resolved address must be public. Malformed input (bad port, over-long IDN label, broken
    IPv6 literal) is an UnsafeDestinationURL too, so callers can answer 422 instead of 500."""
    try:
        parts = urlsplit(url)
        port = parts.port or 443
    except ValueError as exc:
        raise UnsafeDestinationURL("destination URL is not valid") from exc
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeDestinationURL("destination URL must be an https URL")
    if parts.username or parts.password:
        raise UnsafeDestinationURL("destination URL must not contain credentials")
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnsafeDestinationURL("destination host could not be resolved") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeDestinationURL("destination host must resolve to a public address")
    # Pin to one address, so prefer IPv4: a host without IPv6 routing can't fall back to another record.
    return next((i[4][0] for i in infos if i[0] == socket.AF_INET), infos[0][4][0])


def validate_destination_url(url: str) -> None:
    """Raise UnsafeDestinationURL unless `url` is an https URL resolving only to public IPs."""
    _resolve_public_ip(url)


def _post(url: str, ip: str, body: bytes, headers: dict) -> tuple[int, bool]:
    """POST to the validated `ip` instead of re-resolving the hostname, closing the DNS-rebinding
    window between validation and connect. TLS still verifies the certificate against the hostname
    (SNI), and the Host header carries it. Returns (status code, is_success)."""
    parts = urlsplit(url)
    host = f"[{ip}]" if ":" in ip else ip
    netloc = f"{host}:{parts.port}" if parts.port else host
    pinned = urlunsplit(("https", netloc, parts.path, parts.query, ""))
    # stream(): only the status is needed, so a hostile endpoint can't make us buffer a huge body.
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
        with client.stream(
            "POST",
            pinned,
            content=body,
            headers={**headers, "Host": parts.netloc},
            extensions={"sni_hostname": parts.hostname},
        ) as resp:
            return resp.status_code, resp.is_success


def build_payload(kind: str, event: str, text: str, data: dict) -> dict:
    if kind == "slack":
        return {"text": text}
    if kind == "teams":
        # Adaptive-card envelope accepted by Teams "Workflows" webhook triggers.
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                    },
                }
            ],
        }
    return {"event": event, "text": text, "data": data, "sent_at": datetime.now(timezone.utc).isoformat()}


def sign(secret: str, body: bytes, timestamp: str) -> str:
    """HMAC-SHA256 over ``"<timestamp>.<body>"``; the timestamp travels in ``X-Clevis-Timestamp`` so a
    receiver can reject stale deliveries (replay) as well as forged ones."""
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


_EMAIL_SUBJECTS = {
    "score_drop": "Clevis: security score dropped for {org}",
    "waiver_expired": "Clevis: check waivers expired for {org}",
    "test": "Clevis: test message for {org}",
}


def verified_member_emails(db: Session, tenant_id: int) -> set[str]:
    """Lower-cased emails of the org's members whose address is verified: the only people an email
    destination may message, so the instance's SMTP can't be pointed at arbitrary addresses."""
    rows = db.execute(
        sql_text(
            "SELECT u.email FROM memberships m JOIN users u ON u.id = m.user_id "
            "WHERE m.tenant_id = :t AND u.email IS NOT NULL AND u.email_verified = true"
        ),
        {"t": tenant_id},
    ).fetchall()
    return {r[0].lower() for r in rows}


def allowed_emails_for(db: Session, tenant_id: int, destinations: list[NotificationDestination]) -> set[str] | None:
    """The verified-member addresses to pass to send(), or None when no destination is an email one
    (so the member query is only paid for when it is needed)."""
    if any(d.kind == "email" for d in destinations):
        return verified_member_emails(db, tenant_id)
    return None


def _send_email(
    dest: NotificationDestination, event: str, text: str, data: dict, allowed: set[str] | None
) -> tuple[bool, str]:
    if not email.is_configured():
        return False, "SMTP is not configured"
    try:
        stored = json.loads(decrypt_job_token(dest.encrypted_url, settings.job_secret_key.get_secret_value()))
    except Exception:  # decrypt/parse failure: don't let one bad destination break the caller
        logger.exception("notification destination %s could not be decoded", dest.id)
        return False, "internal error"
    # Recipients are re-checked against the org's current verified members on every send, so someone who
    # has since left (or lost verification) stops receiving alerts without anyone editing the destination.
    recipients = [r for r in stored if allowed is None or r.lower() in allowed]
    if not recipients:
        return False, "no eligible recipients"
    subject = _EMAIL_SUBJECTS.get(event, "Clevis alert").format(org=data.get("org", "your organization"))
    sent = 0
    for address in recipients:
        try:
            email.send_email(address, subject, text)
            sent += 1
        except Exception:
            logger.exception("notification email to a recipient of destination %s failed", dest.id)
    # Delivered to anyone counts as sent: a False here releases the alert claim and the next scan would
    # message everyone again, repeating it to the recipients who already got it. The detail still says
    # how many of them did (and the failures are logged).
    return sent > 0, f"{sent}/{len(recipients)} sent"


def send(
    dest: NotificationDestination,
    event: str,
    text: str,
    data: dict | None = None,
    *,
    email_allowed: set[str] | None = None,
) -> tuple[bool, str]:
    """Deliver one message. Returns (ok, short reason); never raises and never echoes the URL.

    `email_allowed` (email destinations): the org's current verified-member addresses, from
    verified_member_emails; recipients outside it are skipped."""
    if dest.kind == "email":
        return _send_email(dest, event, text, data or {}, email_allowed)
    key = settings.job_secret_key.get_secret_value()
    try:
        url = decrypt_job_token(dest.encrypted_url, key)
        ip = _resolve_public_ip(url)
        body = json.dumps(build_payload(dest.kind, event, text, data or {})).encode()
        headers = {"Content-Type": "application/json", "X-Clevis-Event": event}
        if dest.kind == "generic" and dest.encrypted_secret:
            timestamp = str(int(time.time()))
            headers["X-Clevis-Timestamp"] = timestamp
            headers["X-Clevis-Signature"] = sign(decrypt_job_token(dest.encrypted_secret, key), body, timestamp)
        status_code, success = _post(url, ip, body, headers)
    except UnsafeDestinationURL as exc:
        return False, str(exc)
    except httpx.HTTPError:
        return False, "request failed"
    except Exception:  # decrypt failures etc.: don't let one bad destination break the caller
        logger.exception("notification destination %s could not be sent", dest.id)
        return False, "internal error"
    return success, f"HTTP {status_code}"


def notify_score_drop(
    db: Session, tenant_id: int, org_login: str, previous: int | None, current: int | None, *, actor: str = "system"
) -> None:
    """Message every enabled destination subscribed to score drops whose threshold is met.

    Best-effort by contract: a delivery problem is audit-logged and swallowed, never raised into
    the scan request that triggered it."""
    if previous is None or current is None:
        return  # a scan with no scored checks has no score to compare
    drop = previous - current
    if drop <= 0:
        return
    text = f"Clevis: security score for {org_login} dropped {drop} points ({previous} -> {current})."
    data = {"org": org_login, "previous": previous, "current": current}
    targets = [
        d
        for d in notification_repo.list_for_tenant(db, tenant_id)
        if d.enabled and EVENT_SCORE_DROP in d.events and drop >= d.min_score_drop
    ]
    # Claim each destination atomically before sending: this is what makes concurrent scans alert once.
    targets = [d for d in targets if notification_repo.claim_for_alert(db, d.id, ALERT_COOLDOWN)]
    if not targets:
        return
    # The claim commits, which expires the rows; reload here so the sender threads don't lazy-load
    # through the shared Session.
    for d in targets:
        db.refresh(d)
    allowed = allowed_emails_for(db, tenant_id, targets)
    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_SENDS) as pool:
        results = list(pool.map(lambda d: send(d, EVENT_SCORE_DROP, text, data, email_allowed=allowed), targets))
    for dest, (ok, detail) in zip(targets, results):
        if not ok:
            notification_repo.release_alert_claim(db, dest.id)
        audit_repo.write(
            db,
            actor=actor,
            action="notification.sent",
            target=org_login,
            payload={"destination_id": dest.id, "kind": dest.kind, "event": EVENT_SCORE_DROP, "ok": ok, "detail": detail},
            tenant_id=tenant_id,
        )


def notify_waiver_expired(
    db: Session, tenant_id: int, org_login: str, expired: list[dict], *, actor: str = "system"
) -> None:
    """Message every enabled destination subscribed to waiver expiries. ``expired`` is a list of
    ``{"check_id", "repos", "owner", "reason"}`` dicts. Best-effort, like notify_score_drop: a delivery
    problem is audit-logged and swallowed. No cooldown claim: expiries are rare and each is reported once."""
    if not expired:
        return
    names = ", ".join(sorted({e["check_id"] for e in expired}))
    text = (
        f"Clevis: {len(expired)} check waiver{'s' if len(expired) != 1 else ''} for {org_login} expired "
        f"({names}). The check{'s' if len(expired) != 1 else ''} count toward the score again on the next scan."
    )
    data = {"org": org_login, "waivers": expired}
    targets = [
        d for d in notification_repo.list_for_tenant(db, tenant_id) if d.enabled and EVENT_WAIVER_EXPIRED in d.events
    ]
    if not targets:
        return
    allowed = allowed_emails_for(db, tenant_id, targets)
    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_SENDS) as pool:
        results = list(pool.map(lambda d: send(d, EVENT_WAIVER_EXPIRED, text, data, email_allowed=allowed), targets))
    for dest, (ok, detail) in zip(targets, results):
        audit_repo.write(
            db,
            actor=actor,
            action="notification.sent",
            target=org_login,
            payload={"destination_id": dest.id, "kind": dest.kind, "event": EVENT_WAIVER_EXPIRED, "ok": ok, "detail": detail},
            tenant_id=tenant_id,
        )
