"""Remembered repository moves (``repo_move_edges``), keyed by GitHub's stable repo id. Callers set the tenant
context first (RLS) and commit; nothing here commits. Names are lowercase ``owner/name``."""

from sqlalchemy import text
from sqlalchemy.orm import Session

_MAX_HOPS = 20


def record(db: Session, tenant_id: int, repo_id: int, old: str, new: str) -> str:
    """Store the move ``old -> new`` and return the name ``old`` ends up at once every move recorded so far for
    this repo is followed, so a move delivered after a later one still lands on the final name."""
    db.execute(
        text(
            """
            INSERT INTO repo_move_edges (tenant_id, github_repo_id, old_name, new_name)
            VALUES (:t, :r, :old, :new)
            ON CONFLICT (tenant_id, github_repo_id, old_name) DO UPDATE SET new_name = EXCLUDED.new_name, created_at = now()
            """
        ),
        {"t": tenant_id, "r": repo_id, "old": old, "new": new},
    )
    edges = dict(
        db.execute(
            text("SELECT old_name, new_name FROM repo_move_edges WHERE tenant_id = :t AND github_repo_id = :r"),
            {"t": tenant_id, "r": repo_id},
        ).all()
    )
    # ponytail: order is inferred from the edges alone; a rename-back loop stops at the first repeated name.
    seen, name = {old}, old
    for _ in range(_MAX_HOPS):
        nxt = edges.get(name)
        if nxt is None or nxt in seen:
            break
        seen.add(nxt)
        name = nxt
    return name


def last_known_name(db: Session, tenant_id: int, repo_id: int) -> str | None:
    """The repo's name after its most recent recorded move (the newest target that was never moved away
    from), or None for a repo with no recorded move: nothing then says what a transfer started from."""
    return db.execute(
        text(
            """
            SELECT new_name FROM repo_move_edges e
            WHERE tenant_id = :t AND github_repo_id = :r
              AND NOT EXISTS (
                SELECT 1 FROM repo_move_edges o
                WHERE o.tenant_id = e.tenant_id AND o.github_repo_id = e.github_repo_id AND o.old_name = e.new_name
              )
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"t": tenant_id, "r": repo_id},
    ).scalar()
