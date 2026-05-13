import json

from sqlalchemy.orm import Session

from src.models.job import Job


def enqueue(db: Session, job_type: str, payload: dict) -> int:
    job = Job(job_type=job_type, payload=json.dumps(payload), status="queued")
    db.add(job)
    db.commit()
    db.refresh(job)
    return job.id


def list_jobs(db: Session, limit: int = 50) -> list[dict]:
    rows = db.query(Job).order_by(Job.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "job_type": r.job_type,
            "status": r.status,
            "result": r.result,
            "created_at": str(r.created_at),
            "updated_at": str(r.updated_at),
        }
        for r in rows
    ]


def mark_done(db: Session, job_id: int, result: str) -> None:
    db.query(Job).filter(Job.id == job_id).update({"status": "done", "result": result})
    db.commit()


def mark_failed(db: Session, job_id: int, error: str) -> None:
    db.query(Job).filter(Job.id == job_id).update({"status": "failed", "result": error})
    db.commit()
