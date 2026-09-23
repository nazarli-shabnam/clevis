"""Read/write helpers for ``automation_repo_settings`` (per-(tenant, repo, feature) opt-in + options).

Callers must set the tenant session context first; RLS scopes every row by ``tenant_id``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.core.db import AutomationRepoSetting


def get(db: Session, tenant_id: int, repo: str, feature: str) -> AutomationRepoSetting | None:
    return db.execute(
        select(AutomationRepoSetting).where(
            AutomationRepoSetting.tenant_id == tenant_id,
            AutomationRepoSetting.repo == repo,
            AutomationRepoSetting.feature == feature,
        )
    ).scalar_one_or_none()


def list_for_feature(db: Session, tenant_id: int, feature: str) -> list[AutomationRepoSetting]:
    return list(
        db.execute(
            select(AutomationRepoSetting)
            .where(
                AutomationRepoSetting.tenant_id == tenant_id,
                AutomationRepoSetting.feature == feature,
            )
            .order_by(AutomationRepoSetting.repo)
        ).scalars()
    )


def upsert(
    db: Session,
    tenant_id: int,
    repo: str,
    feature: str,
    *,
    enabled: bool,
    mode: str | None = None,
    extra: dict | None = None,
) -> AutomationRepoSetting:
    # Single-statement upsert avoids the get-then-add race on the composite PK. `extra` is only
    # overwritten when supplied, so `extra=None` keeps the stored value.
    values = {
        "tenant_id": tenant_id,
        "repo": repo,
        "feature": feature,
        "enabled": enabled,
        "mode": mode,
    }
    # Core upsert bypasses the ORM, so `updated_at`'s `onupdate` won't fire; bump it explicitly.
    set_ = {"enabled": enabled, "mode": mode, "updated_at": func.now()}
    if extra is not None:
        values["extra"] = extra
        set_["extra"] = extra
    stmt = (
        pg_insert(AutomationRepoSetting)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["tenant_id", "repo", "feature"], set_=set_
        )
    )
    db.execute(stmt)
    row = get(db, tenant_id, repo, feature)
    assert row is not None
    db.refresh(row)  # core upsert bypasses the identity map — pull fresh column values
    return row
