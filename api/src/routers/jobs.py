from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.core.db import get_db
from src.repositories import job_repo
from src.schemas.job import JobOut

router = APIRouter()


@router.get("", response_model=list[JobOut])
def jobs(db: Session = Depends(get_db)):
    return job_repo.list_jobs(db)
