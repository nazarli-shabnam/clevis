"""security_alerts write path: an upsert, since GitHub redelivers on every state change."""

from datetime import datetime

import psycopg
from psycopg.types.json import Json


def upsert_security_alert(
    cur: psycopg.Cursor,
    *,
    tenant_id: int,
    repo: str,
    kind: str,
    number: int,
    state: str,
    severity: str | None,
    details: dict,
    created_at: datetime,
    updated_at: datetime,
) -> bool:
    """Upsert a security_alerts row keyed on (tenant_id, repo, kind, number).

    Returns True iff a new row was inserted. The update only applies when the incoming
    `updated_at` is at least as new as the stored one (delivery order isn't guaranteed).
    Caller must `SET app.tenant_id` on this connection first (RLS WITH CHECK).
    """
    cur.execute(
        """
        INSERT INTO security_alerts
            (tenant_id, repo, kind, number, state, severity, details, created_at, updated_at)
        VALUES (%(tenant_id)s, %(repo)s, %(kind)s, %(number)s, %(state)s, %(severity)s,
                %(details)s, %(created_at)s, %(updated_at)s)
        ON CONFLICT (tenant_id, repo, kind, number)
        DO UPDATE SET
            state = EXCLUDED.state,
            severity = EXCLUDED.severity,
            details = EXCLUDED.details,
            updated_at = EXCLUDED.updated_at
        WHERE EXCLUDED.updated_at >= security_alerts.updated_at
        RETURNING (xmax = 0) AS inserted
        """,
        {
            "tenant_id": tenant_id,
            "repo": repo,
            "kind": kind,
            "number": number,
            "state": state,
            "severity": severity,
            "details": Json(details),
            "created_at": created_at,
            "updated_at": updated_at,
        },
    )
    row = cur.fetchone()
    return row[0] if row is not None else False
