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


@router.post("/actions/news-fetch", response_model=JobStarted, tags=["actions"])
def news_fetch_action(season: str = "2026/27", flush_every: int = 5, concurrency: int = 4) -> JobStarted:
    """Fetch weekly news sentiment for the season's quotati players (Claude Agent SDK)."""

    def job(reporter: BufferingReporter) -> object:
        import asyncio
        from datetime import date
        from types import SimpleNamespace

        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.persistence.news_pool import load_pool
        from fantabot.adapters.persistence.repositories.sentiment import SentimentRepository
        from fantabot.application.news_fetcher import fetch_all
        from fantabot.config import settings
        from fantabot.domain.news.sink import SentimentSink

        model = settings.resolve_agent_model("")
        today = date.today()  # noqa: DTZ011 — local date, matches the CLI's resume key

        with database_manager.get_session() as session:
            players = load_pool(session, season)
            seen = SentimentRepository(session).existing_keys(today)
        players = [player for player in players if (today.isoformat(), player.id) not in seen]

        if not players:
            reporter.print("Nothing to do — every player already has a row for today.")
            return SimpleNamespace(ok=True)

        reporter.print(f"Querying {len(players)} players (model {model})...")

        def flush(rows: list[dict[str, str]]) -> int:
            with database_manager.get_session() as session:
                return int(SentimentRepository(session).upsert_rows(rows, force=False))

        sink = SentimentSink(flush, every=flush_every)

        def on_result(progress: object) -> None:
            row = progress.outcome.row  # type: ignore[attr-defined]
            if row is not None:
                sink.add(row)

        result = asyncio.run(
            fetch_all(
                players,
                concurrency=concurrency,
                today=today,
                model=model,
                stagione=season,
                on_result=on_result,
            )
        )
        sink.drain()
        reporter.print(f"Done: {sink.stored} readings stored, {len(result.failures)} failures.")
        return result

    return JobStarted(job_id=registry.start(job))
