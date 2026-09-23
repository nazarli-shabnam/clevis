"""Shared repo_events + repo_event_daily_counts write path for event_consumer and backfill."""

from datetime import datetime, timezone

import psycopg


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
