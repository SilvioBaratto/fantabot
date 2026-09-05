"""Poll a background job started by an action endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fantabot_app.api.infrastructure.jobs import registry

router = APIRouter()


class JobStatus(BaseModel):
    id: str
    status: str
    lines: list[str]
    ok: bool | None = None
    error: str | None = None
    #: True while the job is parked on a confirmation. A login may ask more than once:
    #: confirming before the browser has written the credential is a normal mistake,
    #: and the UI re-enables its button on this rather than on the log text.
    awaiting_confirm: bool = False


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
        awaiting_confirm=job.awaiting_confirm,
    )
