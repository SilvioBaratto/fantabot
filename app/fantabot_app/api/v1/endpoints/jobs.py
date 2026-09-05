"""Poll, list and stop a background job started by an action endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fantabot_app.api.infrastructure.jobs import registry

router = APIRouter()


class JobStatus(BaseModel):
    id: str
    status: str
    lines: list[str]
    #: Where to resume from. The client sends it back as `?since=`, so a 1.5 s poll costs
    #: what happened since the last one instead of the whole log every time.
    next_index: int = 0
    ok: bool | None = None
    error: str | None = None
    #: True while the job is parked on a confirmation. A login may ask more than once:
    #: confirming before the browser has written the credential is a normal mistake,
    #: and the UI re-enables its button on this rather than on the log text.
    awaiting_confirm: bool = False


class JobSummaryOut(BaseModel):
    """One row of the listing. No `lines` — see `JobSummary`."""

    id: str
    kind: str
    status: str
    started_at: str
    line_count: int
    ok: bool | None = None
    stoppable: bool = False


class JobList(BaseModel):
    jobs: list[JobSummaryOut]


@router.get("/jobs", response_model=JobList, tags=["jobs"])
def list_jobs() -> JobList:
    """Every job the process remembers, newest first.

    This is what lets a page refresh reattach instead of orphaning a running job
    invisibly — and re-enabling the button that would start a second one.
    """
    return JobList(jobs=[JobSummaryOut(**vars(summary)) for summary in registry.list()])


@router.get("/jobs/{job_id}", response_model=JobStatus, tags=["jobs"])
def get_job(job_id: str, since: int = 0) -> JobStatus:
    """One job. With `?since=N`, only the lines from N onward.

    `since` defaults to 0, so a caller that does not pass it gets the whole log exactly as
    before — the UI is migrated separately from the endpoint.
    """
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    lines, next_index = registry.lines_since(job_id, since)
    return JobStatus(
        id=job.id,
        status=job.status,
        lines=lines,
        next_index=next_index,
        ok=job.ok,
        error=job.error,
        awaiting_confirm=job.awaiting_confirm,
    )


@router.post("/jobs/{job_id}/stop", tags=["jobs"])
def stop_job(job_id: str) -> dict[str, bool]:
    """Ask a job to stop, or say plainly that it cannot be.

    Every job today is a daemon thread, and a thread cannot be interrupted from outside.
    409 with a reason is the honest answer; a button that appeared to work and did nothing
    would be worse than no button. Jobs that *can* stop set a callable when they start.
    """
    try:
        stopped = registry.stop(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found") from None
    if not stopped:
        raise HTTPException(status_code=409, detail="this job cannot be stopped")
    return {"ok": True}
