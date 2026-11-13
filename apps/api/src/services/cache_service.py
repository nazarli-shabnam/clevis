from sqlalchemy.orm import Session

from src.repositories import audit_repo, job_repo
from src.schemas.cache import CacheClearInput
from src.services.job_token import job_token_fields


def clear(db: Session, owner: str, repo: str, payload: CacheClearInput, actor: str, token: str, tenant_id: int | None = None) -> dict:
    target = f"{owner}/{repo}"
    if payload.dry_run:
        audit_repo.write(db, actor, "cache.clear.dry_run", target, payload.model_dump(exclude={"token"}), tenant_id=tenant_id)
        return {"queued": False, "dry_run": True, "message": "Dry run completed."}

    # The job (it carries the encrypted token and runs as soon as the worker sees it) and its audit
    # row commit together: a crash or failure between them must not leave a cache clear with no trail.
    job_id = job_repo.enqueue(db, "github.clear_actions_cache", {
        "owner": owner,
        "repo": repo,
        **job_token_fields(token),
        "key": payload.key,
        "ref": payload.ref,
        "actor": actor,
    }, tenant_id=tenant_id, commit=False)
    audit_repo.write(
        db,
        actor,
        "cache.clear.queued",
        target,
        {"job_id": job_id, **payload.model_dump(exclude={"token"})},
        tenant_id=tenant_id,
        commit=False,
    )
    db.commit()
    return {"queued": True, "job_id": job_id}
