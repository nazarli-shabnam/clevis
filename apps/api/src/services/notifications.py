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
from datetime import datetime, timezone
from urllib.parse import urlsplit

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


class UnsafeDestinationURL(ValueError):
    pass


def validate_destination_url(url: str) -> None:
    """Raise UnsafeDestinationURL unless `url` is an https URL resolving only to public IPs.

    ponytail: validate-then-connect leaves a DNS-rebinding window; pin the resolved IP (custom
    transport) if destinations ever need to be accepted from fully untrusted users.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeDestinationURL("destination URL must be an https URL")
    if parts.username or parts.password:
        raise UnsafeDestinationURL("destination URL must not contain credentials")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeDestinationURL("destination host could not be resolved") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeDestinationURL("destination host must resolve to a public address")


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


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def send(dest: NotificationDestination, event: str, text: str, data: dict | None = None) -> tuple[bool, str]:
    """Deliver one message. Returns (ok, short reason); never raises and never echoes the URL."""
    key = settings.job_secret_key.get_secret_value()
    try:
        url = decrypt_job_token(dest.encrypted_url, key)
        validate_destination_url(url)
        body = json.dumps(build_payload(dest.kind, event, text, data or {})).encode()
        headers = {"Content-Type": "application/json", "X-Clevis-Event": event}
        if dest.kind == "generic" and dest.encrypted_secret:
            headers["X-Clevis-Signature"] = sign(decrypt_job_token(dest.encrypted_secret, key), body)
        resp = httpx.post(url, content=body, headers=headers, timeout=_TIMEOUT_SECONDS, follow_redirects=False)
    except UnsafeDestinationURL as exc:
        return False, str(exc)
    except httpx.HTTPError:
        return False, "request failed"
    except Exception:  # decrypt failures etc.: don't let one bad destination break the caller
        logger.exception("notification destination %s could not be sent", dest.id)
        return False, "internal error"
    return (True, f"HTTP {resp.status_code}") if resp.is_success else (False, f"HTTP {resp.status_code}")


def notify_score_drop(db: Session, tenant_id: int, org_login: str, previous: int, current: int) -> None:
    """Message every enabled destination subscribed to score drops whose threshold is met.

    Best-effort by contract: a delivery problem is audit-logged and swallowed, never raised into
    the scan request that triggered it."""
    drop = previous - current
    if drop <= 0:
        return
    text = f"Clevis: security score for {org_login} dropped {drop} points ({previous} -> {current})."
    for dest in notification_repo.list_for_tenant(db, tenant_id):
        if not dest.enabled or EVENT_SCORE_DROP not in dest.events or drop < dest.min_score_drop:
            continue
        ok, detail = send(dest, EVENT_SCORE_DROP, text, {"org": org_login, "previous": previous, "current": current})
        audit_repo.write(
            db,
            actor="system",
            action="notification.sent",
            target=org_login,
            payload={"destination_id": dest.id, "kind": dest.kind, "event": EVENT_SCORE_DROP, "ok": ok, "detail": detail},
            tenant_id=tenant_id,
        )
