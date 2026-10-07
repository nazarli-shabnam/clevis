from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class NotificationItem(BaseModel):
    # Stable across requests (kind plus the source row), so the UI can key on it.
    id: str
    kind: Literal["critical_alert", "score_drop", "job_failed", "permission_drift"]
    at: datetime
    title: str
    detail: str = ""
    # App route to open for this item.
    href: str
    read: bool


class NotificationFeed(BaseModel):
    org: str
    items: list[NotificationItem]
    unread_count: int
    # None until the user first marks this org's notifications read.
    last_read_at: datetime | None = None
