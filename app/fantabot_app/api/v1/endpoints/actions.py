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

    return JobStarted(job_id=registry.start(job, kind="lega-sync"))


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

    return JobStarted(job_id=registry.start(job, kind="news-fetch"))


@router.post("/actions/harvest-scan", response_model=JobStarted, tags=["actions"])
def harvest_scan_action() -> JobStarted:
    """Ask FantaLab which auctions are live and merge them into the harvest home's seed.

    **No format filter, deliberately, and none is reachable from here.** Filtering is a
    query, never a decision taken at collection time: the poller filtering to Mantra is
    what threw away 85% of the population. The CLI's `--only` is not mirrored.

    `AuthExpired` and `ScanEmpty` fail the job carrying their own messages rather than
    being flattened into an empty result — both are refusals, and reporting zero
    auctions would look exactly like a quiet night.
    """

    def job(reporter: BufferingReporter) -> object:
        import json

        from fantabot.adapters.http.harvest.client import LiveAuctionsClient
        from fantabot.adapters.persistence import database_manager
        from fantabot.adapters.tokens.fantalab_store import FantalabStore
        from fantabot.config import harvest_dir, settings
        from fantabot.domain.harvest.registry import from_seed_row, merge, to_seed_rows
        from fantabot.domain.tokens.crypto import TokenCipher

        seed = harvest_dir() / "seed.json"
        cipher = TokenCipher(settings.fantabot_encryption_key)
        # Built inside the session so the bearer is resolved by the adapter and never
        # lands in a local here — the app never handles a plaintext FantaLab token.
        with database_manager.get_session() as session:
            client = LiveAuctionsClient.from_store(FantalabStore(session, cipher))

        scanned = client.live_auctions()

        known = []
        if seed.exists():
            # The poller-era file has no format column, and everything in it was Mantra.
            known = [
                from_seed_row(row, asta_type="mantra")
                for row in json.loads(seed.read_text(encoding="utf-8"))
            ]

        merged = merge(known, scanned)
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(
            json.dumps(to_seed_rows(merged), ensure_ascii=False, indent=0) + "\n",
            encoding="utf-8",
        )

        formats: dict[str, int] = {}
        for config in scanned:
            formats[config.asta_type] = formats.get(config.asta_type, 0) + 1
        reporter.print(
            f"live {len(scanned)} ({', '.join(f'{k} {v}' for k, v in sorted(formats.items()))})"
            f" · registry {len(known)} -> {len(merged)} (+{len(merged) - len(known)})"
        )
        return True

    return JobStarted(job_id=registry.start(job, kind="harvest-scan"))
