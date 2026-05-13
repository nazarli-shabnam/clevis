from pydantic import BaseModel


class JobOut(BaseModel):
    id: int
    job_type: str
    status: str
    result: str | None
    created_at: str
    updated_at: str
