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
from sqlalchemy.orm import Session

from src.core._crypto import decrypt_job_token
from src.core.config import settings
from src.core.db import NotificationDestination
from src.repositories import audit_repo, notification_repo

logger = logging.getLogger(__name__)

KINDS = ("slack", "teams", "generic")
EVENT_SCORE_DROP = "score_drop"
EVENTS = (EVENT_SCORE_DROP,)
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


def send(dest: NotificationDestination, event: str, text: str, data: dict | None = None) -> tuple[bool, str]:
    """Deliver one message. Returns (ok, short reason); never raises and never echoes the URL."""
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
    db: Session, tenant_id: int, org_login: str, previous: int, current: int, *, actor: str = "system"
) -> None:
    """Message every enabled destination subscribed to score drops whose threshold is met.

    Best-effort by contract: a delivery problem is audit-logged and swallowed, never raised into
    the scan request that triggered it."""
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
    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_SENDS) as pool:
        results = list(pool.map(lambda d: send(d, EVENT_SCORE_DROP, text, data), targets))
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
