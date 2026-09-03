"""Poll a background job started by an action endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.infrastructure.jobs import registry

router = APIRouter()


class JobStatus(BaseModel):
    id: str
    status: str
    lines: list[str]
    ok: bool | None = None
    error: str | None = None


@router.get("/jobs/{job_id}", response_model=JobStatus, tags=["jobs"])
def get_job(job_id: str) -> JobStatus:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatus(
        id=job.id,
        status=job.status,
        lines=list(job.lines),
        ok=job.ok,
        error=job.error,
    )
