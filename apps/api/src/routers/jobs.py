from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.auth import UserOut, require_owner
from src.core.db import get_db
from src.repositories import job_repo
from src.schemas.job import JobOut

router = APIRouter()


@router.get("", response_model=list[JobOut])
def jobs(db: Session = Depends(get_db), _user: UserOut = Depends(require_owner)):
    return job_repo.list_jobs(db)
