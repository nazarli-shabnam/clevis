"""Shared repo_events + repo_event_daily_counts write path for event_consumer and backfill."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import psycopg

# Webhook rows are stamped with our receive time, not GitHub's created_at, so a backfilled event and
# its webhook copy never share a timestamp exactly; they land within seconds of each other.
WEBHOOK_TWIN_WINDOW = timedelta(minutes=2)

TwinKey = tuple[str, str, str]  # (repo, event_type, actor)


def webhook_events_between(cur: psycopg.Cursor, *, tenant_id: int, start: datetime, end: datetime) -> dict[TwinKey, list[datetime]]:
    """Webhook-sourced rows (not backfilled ones) near [start, end], grouped for twin matching.

    Backfill ids are 'backfill:<event id>' while webhook ids are GitHub's delivery GUIDs, so
    ON CONFLICT (delivery_id) cannot see that both describe the same real event."""
    cur.execute(
        """
        SELECT repo, event_type, actor, occurred_at FROM repo_events
        WHERE tenant_id = %s AND delivery_id NOT LIKE 'backfill:%%' AND occurred_at BETWEEN %s AND %s
        """,
        (tenant_id, start - WEBHOOK_TWIN_WINDOW, end + WEBHOOK_TWIN_WINDOW),
    )
    index: dict[TwinKey, list[datetime]] = defaultdict(list)
    for repo, event_type, actor, occurred_at in cur.fetchall():
        index[(repo, event_type, actor)].append(occurred_at)
    return index


def consume_webhook_twin(index: dict[TwinKey, list[datetime]], *, repo: str, event_type: str, actor: str, occurred_at: datetime) -> bool:
    """True if a webhook row for the same repo/type/actor is within the window; that row is used up,
    so two backfilled events only match two webhook rows (a lone webhook row can't hide both)."""
    times = index.get((repo, event_type, actor), [])
    for i, seen_at in enumerate(times):
        if abs(seen_at - occurred_at) <= WEBHOOK_TWIN_WINDOW:
            del times[i]
            return True
    return False


def insert_event_and_upsert_daily_count(
    cur: psycopg.Cursor,
    *,
    tenant_id: int,
    delivery_id: str,
    event_type: str,
    actor: str,
    actor_avatar: str,
    repo: str,
    summary: str,
    occurred_at: datetime,
) -> bool:
    """Insert a repo_events row (ON CONFLICT (delivery_id) DO NOTHING) and, only if
    inserted, upsert the daily count. Returns True iff a new row was inserted.

    Caller must `SET app.tenant_id` on this connection first (RLS WITH CHECK).
    """
    cur.execute(
        """
        INSERT INTO repo_events
            (tenant_id, delivery_id, event_type, actor, actor_avatar, repo, summary, occurred_at)
        VALUES (%(tenant_id)s, %(delivery_id)s, %(event_type)s, %(actor)s, %(actor_avatar)s,
                %(repo)s, %(summary)s, %(occurred_at)s)
        ON CONFLICT (delivery_id) DO NOTHING
        RETURNING id
        """,
        {
            "tenant_id": tenant_id,
            "delivery_id": delivery_id,
            "event_type": event_type,
            "actor": actor,
            "actor_avatar": actor_avatar,
            "repo": repo,
            "summary": summary,
            "occurred_at": occurred_at,
        },
    )
    inserted = cur.fetchone() is not None
    if inserted:
        cur.execute(
            """
            INSERT INTO repo_event_daily_counts (tenant_id, repo, event_type, day, count)
            VALUES (%(tenant_id)s, %(repo)s, %(event_type)s, %(day)s, 1)
            ON CONFLICT (tenant_id, repo, event_type, day)
            DO UPDATE SET count = repo_event_daily_counts.count + 1
            """,
            {
                "tenant_id": tenant_id,
                "repo": repo,
                "event_type": event_type,
                # The session timezone may not be UTC; force UTC so "day" is the UTC calendar day.
                "day": occurred_at.astimezone(timezone.utc).date(),
            },
        )
    return inserted
