from sqlalchemy.orm import Session

from src.repositories import audit_repo, job_repo
from src.schemas.cache import CacheClearInput


def clear(db: Session, owner: str, repo: str, payload: CacheClearInput) -> dict:
    target = f"{owner}/{repo}"
    if payload.dry_run:
        audit_repo.write(db, payload.actor, "cache.clear.dry_run", target, payload.model_dump())
        return {"queued": False, "dry_run": True, "message": "Dry run completed."}

    job_id = job_repo.enqueue(db, "github.clear_actions_cache", {
        "owner": owner,
        "repo": repo,
        "token": payload.token,
        "key": payload.key,
        "ref": payload.ref,
        "actor": payload.actor,
    })
    audit_repo.write(db, payload.actor, "cache.clear.queued", target, {"job_id": job_id, **payload.model_dump()})
    return {"queued": True, "job_id": job_id}
