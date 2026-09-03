"""Safe trigger actions — run a fantabot use case as a background job.

Each POST starts a job (the in-process runner) and returns its id; the UI polls
GET /jobs/{id}. lega-sync reads the platform then persists, in two separate sessions
(fantabot's reads-and-writes-are-separate-phases rule). A missing key/token makes the job
fail cleanly — never a 500 on the trigger itself. Re-running is safe: every fantabot write
is an upsert.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from fantabot_app.api.infrastructure.jobs import BufferingReporter, registry

router = APIRouter()


class JobStarted(BaseModel):
    job_id: str


@router.post("/actions/lega-sync", response_model=JobStarted, tags=["actions"])
def lega_sync_action(league_id: int) -> JobStarted:
    """Read the whole lega and persist it (8 reads, 6 tables)."""

    def job(reporter: BufferingReporter) -> object:
        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.repositories.league import LeagueRepository
        from fantabot.adapters.tokens.store import TokenStore
        from fantabot.application.lega_sync import collect, persist
        from fantabot.config import settings
        from fantabot.domain.tokens.crypto import TokenCipher

        cipher = TokenCipher(settings.fantabot_encryption_key)
        # Read phase (network; the session is only for the token read).
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            result = collect(league_id, store=store, reporter=reporter)
        # Write phase (separate session — no write txn held across the multi-MB GETs).
        with database_manager.get_session() as session:
            written = persist(result, LeagueRepository(session))
        reporter.print(f"wrote {sum(written.values())} rows across {len(written)} tables")
        return result

    return JobStarted(job_id=registry.start(job))
