"""Which orgs are scanned on a schedule, and how often.

The instance-wide ``scheduled_scan_cadence`` (off, daily, weekly) is the default; an org's own
``orgs.scheduled_scans`` overrides it (True = on, False = off, NULL = follow the instance). An org that
opts in while the instance cadence is off runs weekly, the gentlest setting."""

from datetime import timedelta
from typing import Literal

from src.core.app_config import get_config

Cadence = Literal["daily", "weekly"]

CADENCE_INTERVAL: dict[str, timedelta] = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
_OPT_IN_DEFAULT: Cadence = "weekly"


def instance_cadence() -> Literal["off", "daily", "weekly"]:
    raw = (get_config("scheduled_scan_cadence", "off") or "off").strip().lower()
    return raw if raw in CADENCE_INTERVAL else "off"  # type: ignore[return-value]


def effective_cadence(override: bool | None, instance: str | None = None) -> Cadence | None:
    """The cadence an org is scanned at, or None when it is not scanned on a schedule."""
    instance = instance if instance is not None else instance_cadence()
    if override is False:
        return None
    if instance in CADENCE_INTERVAL:
        return instance  # type: ignore[return-value]
    return _OPT_IN_DEFAULT if override is True else None
