"""Run a long request's work off the request thread, tracked in the `jobs` table.

Scans and the bulk GitHub automations loop over many repos and used to run inside the HTTP request, so a
slow GitHub tied up a client connection and a pooled DB connection for minutes. With `?async=1` the router
validates and authorizes as before, writes its audit row, then calls `submit()`: the work runs on a small
bounded pool in this process with its own DB session, and the client polls `GET /jobs/{id}`.

API-run jobs are inserted as ``running``, never ``queued``: the worker claims every ``queued`` row and would
fail one with no handler. If the API restarts mid-job the row stays ``running``; `reap_orphans` (retention
sweep) marks it failed once it is old enough that it cannot still be alive.
"""

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.core.db import Job, SessionLocal, set_session_tenant, set_session_user
from src.repositories import job_repo

log = logging.getLogger(__name__)

JOB_TYPE_ANALYTICS = "api.analytics_overview"
JOB_TYPE_REMEDIATE = "api.remediate_bulk"
JOB_TYPE_BRANCH_PROTECTION = "api.branch_protection_bulk"
JOB_TYPE_DEPENDABOT_TRIAGE = "api.dependabot_triage"
API_JOB_TYPES = frozenset(
    {JOB_TYPE_ANALYTICS, JOB_TYPE_REMEDIATE, JOB_TYPE_BRANCH_PROTECTION, JOB_TYPE_DEPENDABOT_TRIAGE}
)

# A running job older than this cannot still be alive (a scan of a huge org takes minutes, not hours).
ORPHAN_AFTER = timedelta(hours=2)

# Bounds how many long GitHub loops run at once; excess jobs wait in the pool's queue, still `running`.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="api-job")


@contextmanager
def _open_session():
    with SessionLocal() as db:
        yield db


def accepted(job_id: int) -> JSONResponse:
    return JSONResponse(status_code=202, content={"job_id": job_id})


def submit(
    db: Session,
    job_type: str,
    *,
    tenant_id: int | None,
    user_id: int,
    actor: str,
    work: Callable[[Session], Any],
) -> int:
    """Record the job (committing the caller's pending audit rows with it) and start `work(session)`.

    `work` gets a fresh session with the tenant/user context set; its return value (a pydantic model or
    JSON-able object) becomes the job result. Raising HTTPException fails the job with its detail."""
    # The payload keeps only the actor: GET /jobs/{id} scopes a non-admin read to it, and no token.
    job_id = job_repo.enqueue(db, job_type, {"actor": actor}, tenant_id, status="running")
    _executor.submit(_run, job_id, tenant_id, user_id, work)
    return job_id


def _run(job_id: int, tenant_id: int | None, user_id: int, work: Callable[[Session], Any]) -> None:
    try:
        with _open_session() as db:
            try:
                if tenant_id is not None:
                    set_session_tenant(db, tenant_id)
                set_session_user(db, user_id)
                result = work(db)
                payload = result.model_dump_json() if isinstance(result, BaseModel) else json.dumps(result, default=str)
                job_repo.mark_done(db, job_id, payload)
            except HTTPException as exc:
                db.rollback()
                job_repo.mark_failed(db, job_id, str(exc.detail))
            except Exception:
                log.exception("api job %s failed", job_id)
                db.rollback()
                job_repo.mark_failed(db, job_id, "Internal error")
            finally:
                # Plain SET survives commit on the pooled connection; clear it like get_db does.
                db.rollback()
                db.execute(text("RESET app.tenant_id"))
                db.execute(text("RESET app.user_id"))
                db.commit()
    except Exception:
        log.exception("api job %s could not record its outcome", job_id)


def reap_orphans(db: Session) -> int:
    """Fail `running` API jobs too old to still be alive (the API restarted under them)."""
    cutoff = datetime.now(timezone.utc) - ORPHAN_AFTER
    reaped = (
        db.query(Job)
        .filter(Job.status == "running", Job.job_type.in_(API_JOB_TYPES), Job.updated_at < cutoff)
        .update(
            {"status": "failed", "result": "Interrupted by an API restart; run it again."},
            synchronize_session=False,
        )
    )
    db.commit()
    return reaped
