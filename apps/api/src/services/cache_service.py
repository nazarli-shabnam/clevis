from sqlalchemy.orm import Session

from src.core._crypto import encrypt_job_token
from src.core.config import settings
from src.repositories import audit_repo, job_repo
from src.schemas.cache import CacheClearInput


def clear(db: Session, owner: str, repo: str, payload: CacheClearInput, actor: str, token: str, tenant_id: int | None = None) -> dict:
    target = f"{owner}/{repo}"
    if payload.dry_run:
        audit_repo.write(db, actor, "cache.clear.dry_run", target, payload.model_dump(exclude={"token"}), tenant_id=tenant_id)
        return {"queued": False, "dry_run": True, "message": "Dry run completed."}

    encrypted_token = encrypt_job_token(
        token,
        settings.job_secret_key.get_secret_value(),
    )
    # The job (it carries the encrypted token and runs as soon as the worker sees it) and its audit
    # row commit together: a crash or failure between them must not leave a cache clear with no trail.
    job_id = job_repo.enqueue(db, "github.clear_actions_cache", {
        "owner": owner,
        "repo": repo,
        "token": encrypted_token,
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
