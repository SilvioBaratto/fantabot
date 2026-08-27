from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

if TYPE_CHECKING:  # annotations only — cli.py must stay import-light
    from datetime import datetime

    import httpx

    from fantabot.tokens.store import TokenStore

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def config_check() -> None:
    """Print resolved settings (secrets masked) — sanity check before running anything."""
    from sqlalchemy.engine import make_url

    from fantabot.config import settings

    # Cron captures stdout, so anything printed here outlives the run in a log
    # file. `lega_password` was being dumped verbatim before the DSN existed —
    # `repr=False` does not suppress `model_dump`, which is why the exclude set
    # is the only thing standing between a secret and the log.
    secrets = {
        "stats_source_api_key",
        "lega_password",
        "fantabot_database_url",
        "fantabot_encryption_key",
        # Harmless on Ollama, where the documented value is the placeholder
        # "ollama". Not harmless behind a gateway, where it is a real bearer
        # token — and config-check cannot tell the two apart, so neither prints.
        "fantabot_agent_auth_token",
    }
    console.print(settings.model_dump(exclude=secrets))
    console.print(f"stats_source_api_key set: {bool(settings.stats_source_api_key)}")
    console.print(f"lega_password set: {bool(settings.lega_password)}")
    console.print(f"fantabot_encryption_key set: {bool(settings.fantabot_encryption_key)}")
    console.print(f"fantabot_agent_auth_token set: {bool(settings.fantabot_agent_auth_token)}")
    # Printed in full, unlike the token: it is routing, not a credential, and an
    # unexpected value here is the fastest explanation for a cron run that went
    # somewhere other than the subscription.
    console.print(
        f"fantabot_agent_base_url: {settings.fantabot_agent_base_url or '(subscription)'}"
    )

    # An invalid DSN should fail loudly here rather than at the first connect.
    dsn = make_url(settings.fantabot_database_url).render_as_string(hide_password=True)
    console.print(f"fantabot_database_url: {dsn}")


@app.command()
def lineup_submit(
    headless: bool = typer.Option(True, help="Run browser headless (cron use)."),
) -> None:
    """Single run: check deadline, pick best XI + captain, submit if not already done."""
    console.print(
        "[red]No StatsSource wired in yet — pick a data source, implement it under "
        "fantabot/data_sources/, then call lineup.run_once(your_source, headless).[/red]"
    )
    raise typer.Exit(code=1)


@app.command()
def news_fetch(
    scope: str = typer.Option("pool", help="Only 'pool' is implemented — see below."),
    write: bool = typer.Option(
        False, "--write", help="Store the readings. Off = query and discard."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-query players that already have today's row."
    ),
    limit: int = typer.Option(0, help="Stop after N players (0 = no limit)."),
    only: str = typer.Option("", help="One player by name, substring match."),
    concurrency: int = typer.Option(4, help="Parallel agent queries."),
    model: str = typer.Option("", help="Model id. Empty = FANTABOT_AGENT_MODEL."),
    season: str = typer.Option("2026/27", help="Which stagione to fetch."),
    lookback_days: int = typer.Option(14, help="Days of news each query should cover."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Show the built prompt."),
    no_run: bool = typer.Option(False, "--no-run", help="Build everything, query nothing."),
) -> None:
    """Fetch weekly news sentiment for the season's quotati players."""
    from fantabot.agentkit.env import strip_dangerous_env
    from fantabot.config import settings
    from fantabot.news.pipeline import fetch_all
    from fantabot.news.pool import load_pool
    from fantabot.news.prompt import build_prompt

    if scope != "pool":
        # Not deferred-and-half-built: reading a roster needs the league API
        # (lineup.scrape_roster is a stub, docs/leghe-api.md has the endpoints),
        # and with two leagues it would also need a --league selector. Falling
        # back to the full pool would spend 523 queries and look like it worked.
        console.print(
            f"[red]--scope {scope!r} is not implemented.[/red] Only 'pool' exists today: "
            "reading your roster needs the apileague.fantacalcio.it endpoints in "
            "docs/leghe-api.md wired up first, plus a --league selector to say which "
            "of your two leagues you mean."
        )
        raise typer.Exit(code=2)

    try:
        model = settings.resolve_agent_model(model)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    from fantabot.db import database_manager
    from fantabot.db.repositories.sentiment import SentimentRepository

    today = date.today()

    # One session for both reads: the pool and the resume filter are the same
    # question asked twice — what is this run going to query?
    with database_manager.get_session() as session:
        players = load_pool(session, season)
        seen = set() if force else SentimentRepository(session).existing_keys(today)

    if only:
        players = [p for p in players if only.lower() in p.nome.lower()]
    if not force:
        players = [p for p in players if (today.isoformat(), p.id) not in seen]
    if limit:
        players = players[:limit]

    if print_prompt:
        for player in players:
            console.print(build_prompt(player, lookback_days, today))

    if no_run:
        console.print(f"[dim]--no-run: {len(players)} players prepared, nothing queried.[/dim]")
        return

    if not players:
        console.print("[green]Nothing to do — every player already has a row for today.[/green]")
        return

    strip_dangerous_env()
    console.print(f"Querying {len(players)} players at concurrency {concurrency}...")
    result = asyncio.run(
        fetch_all(
            players,
            concurrency=concurrency,
            lookback_days=lookback_days,
            today=today,
            model=model,
            stagione=season,
        )
    )

    for name, reason in result.failures:
        console.print(f"[yellow]failed[/yellow] {name}: {reason}")
    if result.rate_limited:
        console.print("[yellow]rate limits were hit; the run backed off and continued[/yellow]")

    if write:
        with database_manager.get_session() as session:
            # force means both "re-query him" and "overwrite what is stored":
            # without it a same-day re-run is a no-op rather than a duplicate.
            stored = SentimentRepository(session).upsert_rows(result.rows, force=force)
        console.print(f"[green]{stored} rows -> player_sentiment[/green]")
    else:
        for row in result.rows:
            console.print(row)
        console.print(
            f"[dim]{len(result.rows)} rows discarded (--write not given), "
            f"{len(result.failures)} failures.[/dim]"
        )


@app.command()
def mantra_grid(
    write: bool = typer.Option(False, "--write", help="Write the JSON files if every gate passes."),
    model: str = typer.Option("", help="Model id. Empty = FANTABOT_AGENT_MODEL."),
) -> None:
    """Collect the 11 Mantra schemas and the out-of-position matrix. One-off, not cron."""
    from fantabot.agentkit.env import strip_dangerous_env
    from fantabot.config import settings
    from fantabot.mantra_grid.collect import CollectError, collect
    from fantabot.mantra_grid.writer import COMPAT_FILENAME, SCHEMI_FILENAME, write_json

    try:
        model = settings.resolve_agent_model(model)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    strip_dangerous_env()
    console.print(f"Collecting the 11 Mantra schemas and the compatibility matrix via {model}...")
    try:
        result = asyncio.run(collect(model))
    except CollectError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not result.ok:
        # Nothing is written. Re-run the collector, or fix the gate if the gate is
        # what is wrong — never hand-patch the output to satisfy the check.
        console.print(f"[red]{len(result.problems)} gate failures — writing nothing:[/red]")
        for problem in result.problems:
            console.print(f"  - {problem}")
        raise typer.Exit(code=1)

    console.print(f"[green]All gates passed: {len(result.grid.schemi)} schemas.[/green]")
    if not write:
        console.print(result.grid.model_dump())
        console.print(result.matrix.model_dump())
        console.print("[dim]--write not given, nothing saved.[/dim]")
        return

    data_dir: Path = settings.fantabot_data_dir
    write_json(data_dir / SCHEMI_FILENAME, result.grid)
    write_json(data_dir / COMPAT_FILENAME, result.matrix)
    console.print(f"[green]-> {data_dir / SCHEMI_FILENAME}[/green]")
    console.print(f"[green]-> {data_dir / COMPAT_FILENAME}[/green]")
    console.print(
        "[yellow]Verify both by hand against rules/sistema-mantra.md before committing.[/yellow]"
    )


@app.command()
def fantalab_login(
    force: bool = typer.Option(False, "--force", help="Re-authenticate even if a session exists."),
    browser: str = typer.Option(
        "", help="Installed browser to drive: msedge, chrome. Empty = bundled Chromium."
    ),
) -> None:
    """Sign in to FantaLab once; store the session encrypted in Postgres.

    Opens a real browser and waits. **This program types nothing and clicks
    nothing** — a scripted sign-in is what gets accounts flagged, and FantaLab
    offers Google and Apple besides its own form, so there is no single flow to
    automate even if that were wanted.

    No `storage_state.json` is written. That file would hold three credentials
    in the clear; they go from browser memory through Fernet into Postgres.
    """
    from fantabot.fantalab_login import LoginAborted
    from fantabot.fantalab_login import run as run_login

    try:
        run_login(force=force, channel=browser or None)
    except LoginAborted as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(exc.code) from None


@app.command()
def aste_load(
    landing: Path = typer.Argument(..., help="Landing-zone JSONL the collector appends to."),
    seed: Path = typer.Option(..., help="The scan seed describing each auction."),
    listone: Path = typer.Option(
        Path("data/aste_live/listone_map.json"),
        help="uuid -> fantacalcio_id bridge from GET /v2/listone.",
    ),
    asta_type: str = typer.Option("mantra", help="Format the seed was collected for."),
    follow: bool = typer.Option(False, "--follow", help="Keep reading as the file grows."),
    interval: float = typer.Option(10.0, help="Seconds between passes when following."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Read and report, write nothing."),
) -> None:
    """Carry the landing zone into Postgres, resuming where the last pass stopped.

    The database is deliberately not on the collection critical path: the
    collector writes to the file and this reads from it. An outage costs
    catch-up time, never a record.
    """
    import json
    import time

    from fantabot.aste.backfill import build
    from fantabot.aste.loader import Checkpoint, read_from
    from fantabot.db.models.aste import ASTA_TYPES

    if asta_type not in ASTA_TYPES:
        console.print(f"[red]{asta_type!r} is not a format. Use one of: {', '.join(ASTA_TYPES)}")
        raise typer.Exit(2)
    if not seed.exists():
        console.print(f"[red]seed file not found: {seed}[/red]")
        raise typer.Exit(2)

    seed_rows = json.loads(seed.read_text(encoding="utf-8"))
    bridge = json.loads(listone.read_text(encoding="utf-8")) if listone.exists() else {}
    checkpoint = Checkpoint(landing)

    def pass_once() -> tuple[int, int]:
        """Records carried, and bytes still behind the writer."""
        offset = checkpoint.read()
        records, new_offset = read_from(landing, offset)
        if not records:
            return 0, 0

        known_players = None
        if not dry_run:
            from fantabot.db import database_manager
            from fantabot.db.repositories.aste import AsteRepository

            with database_manager.get_session() as session:
                known_players = AsteRepository(session).known_player_ids()

        # The same build() the backfill uses. A loader that grew its own would
        # leave one of the two untested.
        auctions, events, assignments, unlinked = build(
            records, seed_rows, bridge, asta_type, known_players
        )
        if not dry_run:
            from fantabot.db import database_manager
            from fantabot.db.repositories.aste import AsteRepository

            with database_manager.get_session() as session:
                repo = AsteRepository(session)
                repo.upsert_auctions(auctions)
                repo.upsert_events(events)
                repo.upsert_assignments(assignments)
                session.commit()
            checkpoint.write(new_offset)

        size = landing.stat().st_size if landing.exists() else new_offset
        if unlinked:
            console.print(f"[yellow]{unlinked} assignment(s) carry no player link[/yellow]")
        return len(records), size - new_offset

    from sqlalchemy.exc import SQLAlchemyError

    while True:
        try:
            carried, behind = pass_once()
        except SQLAlchemyError as exc:
            # The checkpoint has not moved, so nothing is lost — the next pass
            # re-reads exactly what this one could not write. Said out loud,
            # because a raw driver traceback tells you the connection failed and
            # not that the collector is fine and the catch-up is pending.
            console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
            console.print("Collection is unaffected — the landing zone keeps growing.")
            console.print("Start it with: [bold]docker compose up -d[/bold], then re-run.")
            if not follow:
                raise typer.Exit(1) from exc
            time.sleep(interval)
            continue

        suffix = " (dry run — nothing written)" if dry_run else ""
        # Lag is reported every pass, not only when it is large: a loader that
        # only speaks up when it is already behind gives no warning it is losing.
        console.print(f"carried {carried} · {behind} bytes behind{suffix}")
        if not follow:
            return
        time.sleep(interval)


@app.command()
def aste_collect(
    auction: str = typer.Option(..., "--one", help="Auction uuid to follow."),
    shard: str = typer.Option(..., help="Firebase shard, from the auction's list card."),
    out: Path = typer.Option(..., help="Landing-zone JSONL to append to."),
) -> None:
    """Subscribe to one live auction and append every state to the landing zone.

    Writes to disk, never to the database. A database outage must not be able to
    stop collection — the file survived eleven process kills on 2026-08-26 and a
    socket would not have.
    """
    import asyncio

    from fantabot.aste.landing import LandingZone
    from fantabot.aste.stream import Outcome, SinkFailed, watch_auction
    from fantabot.aste.transport import open_stream

    zone = LandingZone(out)
    console.print(f"following {auction[:8]} on fantalab-{shard} -> {out}")

    async def run() -> Outcome:
        return await watch_auction(
            auction,
            shard,
            open_stream=open_stream,
            on_state=lambda state: zone.write(auction, state),
            sleep=asyncio.sleep,
        )

    try:
        outcome = asyncio.run(run())
    except SinkFailed as exc:
        # Not a transport problem, and not survivable by reconnecting: if the
        # sink is failing, continuing would reconnect forever and store nothing.
        console.print(f"[red]the landing zone failed: {exc}[/red]")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        console.print(f"[yellow]stopped — {zone.written} states written[/yellow]")
        return

    colour = "green" if outcome is Outcome.ENDED else "yellow"
    console.print(f"[{colour}]{outcome.value} — {zone.written} states written[/{colour}]")


@app.command()
def aste_backfill(
    events: Path = typer.Argument(..., help="Collector log: one merged state per line."),
    seed: Path = typer.Option(..., help="The scan seed describing each auction."),
    listone: Path = typer.Option(
        Path("data/aste_live/listone_map.json"),
        help="uuid -> fantacalcio_id bridge from GET /v2/listone.",
    ),
    asta_type: str = typer.Option("mantra", help="Format the seed was collected for."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build and report, write nothing."),
) -> None:
    """Load a recorded collector log into `asta`, `asta_event` and `asta_assignment`.

    The same code path the live loader uses. A backfill that grows its own way of
    building rows leaves one of the two untested, and the difference shows up on
    an evening that cannot be collected twice.
    """
    import json

    from fantabot.aste.backfill import build, read_jsonl
    from fantabot.db.models.aste import ASTA_TYPES

    # Checked before any work: asta_type is NOT NULL and only two values exist,
    # so a typo caught here beats a constraint violation after building 144,518
    # rows.
    if asta_type not in ASTA_TYPES:
        console.print(f"[red]{asta_type!r} is not a format. Use one of: {', '.join(ASTA_TYPES)}")
        raise typer.Exit(2)
    for label, path in (("events", events), ("seed", seed)):
        if not path.exists():
            console.print(f"[red]{label} file not found: {path}[/red]")
            raise typer.Exit(2)

    states = read_jsonl(events)
    seed_rows = json.loads(seed.read_text(encoding="utf-8"))
    bridge = json.loads(listone.read_text(encoding="utf-8")) if listone.exists() else {}
    if not bridge:
        console.print(f"[yellow]no listone at {listone}; assignments will carry no player link")

    known_players: frozenset[int] | None = None
    if not dry_run:
        from fantabot.db import database_manager
        from fantabot.db.repositories.aste import AsteRepository

        with database_manager.get_session() as session:
            known_players = AsteRepository(session).known_player_ids()

    auctions, event_rows, assignments, unlinked = build(
        states, seed_rows, bridge, asta_type, known_players
    )
    console.print(
        f"auctions {len(auctions)} · events {len(event_rows)} from {len(states)} states"
        f" · assignments {len(assignments)}"
    )
    if unlinked:
        # A staleness signal, not a warning to scroll past: a few is a transfer
        # window, a lot means the reference table no longer describes the listone.
        console.print(
            f"[yellow]{unlinked} assignment(s) carry no player link — "
            "`players` is behind the listone[/yellow]"
        )

    if dry_run:
        console.print("[yellow]dry run — nothing written[/yellow]")
        return

    from fantabot.db import database_manager
    from fantabot.db.repositories.aste import AsteRepository

    with database_manager.get_session() as session:
        repo = AsteRepository(session)
        repo.upsert_auctions(auctions)
        repo.upsert_events(event_rows)
        repo.upsert_assignments(assignments)
        session.commit()
        console.print(f"[green]stored — {repo.count_assignments()} assignments in total")


@app.command()
def db_check() -> None:
    """Database health, plus a row count and on-disk size for every table."""
    from rich.table import Table
    from sqlalchemy.exc import SQLAlchemyError

    import fantabot.db.models  # noqa: F401  -- registers every table on Base.metadata
    from fantabot.config import settings
    from fantabot.db import database_manager
    from fantabot.db.repositories.admin import AdminRepository

    try:
        with database_manager.get_session() as session:
            repo = AdminRepository(session)
            ok, latency_ms = repo.health()
            stats = repo.table_stats()
    except SQLAlchemyError as exc:
        # An unreachable database is the normal case this command exists to
        # report, so it exits nonzero with an instruction rather than a stack
        # trace. The DSN is masked: cron captures stdout.
        from sqlalchemy.engine import make_url

        dsn = make_url(settings.fantabot_database_url).render_as_string(hide_password=True)
        console.print(f"[red]Cannot reach the database at {dsn}[/red]")
        console.print(f"[red]{type(exc).__name__}: {str(exc).splitlines()[0]}[/red]")
        console.print("Start it with: [bold]docker compose up -d[/bold]")
        raise typer.Exit(code=1) from None

    status = "[green]ok[/green]" if ok else "[red]unhealthy[/red]"
    console.print(f"health: {status}  latency: {latency_ms} ms")

    table = Table("table", "rows", "size")
    for row in stats:
        rows = "—" if row["row_count"] is None else f"{row['row_count']:,}"
        table.add_row(row["name"], rows, row["size_pretty"])
    console.print(table)

    missing = [row["name"] for row in stats if not row["exists"]]
    if missing:
        console.print(
            f"[yellow]{len(missing)} table(s) declared but not in the database: "
            f"{', '.join(missing)}. Run: [bold]alembic upgrade head[/bold][/yellow]"
        )


@app.command()
def db_import(
    every: bool = typer.Option(False, "--all", help="Load every table, in dependency order."),
    table: str | None = typer.Option(None, "--table", help="Load one table by name."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report the plan and the row counts, write nothing."
    ),
) -> None:
    """Seed Postgres from the CSVs in data/. Idempotent — safe to re-run.

    Neither --all nor --table has a default: naming what to load is the
    safe-by-default posture here. SPEC's Commands section once said --dry-run
    was the default instead, but success criterion 6 requires bare
    `fantabot db-import --all` to load, and SC 6 is the testable one. SPEC was
    amended on 2026-08-26 to match.
    """
    from fantabot.config import settings
    from fantabot.db import importers

    if every and table is not None:
        console.print("[red]Pass --all or --table, not both.[/red]")
        raise typer.Exit(code=2)
    if not every and table is None:
        console.print(
            "[red]Nothing named. Pass --all, or --table with one of: "
            f"{', '.join(importers.names()) or '(none registered yet)'}[/red]"
        )
        raise typer.Exit(code=2)

    try:
        selected = importers.resolve(every=every, table=table)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/red]")
        raise typer.Exit(code=2) from None

    if not selected:
        console.print("[yellow]No importers registered yet.[/yellow]")
        return

    data_dir = settings.fantabot_data_dir
    plan = [(imp, imp.missing_sources(data_dir)) for imp in selected]

    for imp, missing in plan:
        expected = "" if imp.expected_rows is None else f" ~{imp.expected_rows:,} rows"
        sources = ", ".join(imp.sources)
        if missing:
            console.print(f"[yellow]{imp.name}: missing {', '.join(missing)} — skipped[/yellow]")
        else:
            console.print(f"{imp.name}: {sources}{expected}")

    if dry_run:
        # Short-circuits before any engine exists: --dry-run must never connect.
        console.print("[cyan]--dry-run: nothing written.[/cyan]")
        return

    from fantabot.db import database_manager

    runnable = [imp for imp, missing in plan if not missing]
    for imp in runnable:
        with database_manager.get_session() as session:
            result = imp.load(session, data_dir)
        console.print(
            f"[green]{result.table}: {result.inserted:,} inserted, "
            f"{result.unchanged:,} unchanged, {result.total:,} total[/green]"
        )


def token_status_rows(
    store: TokenStore,
    *,
    now: datetime,
    verify: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> list[tuple[str, str, str, str]]:
    """The rendered table body. **This is the injection point.**

    A Typer command has nowhere to accept a transport, so the work lives here
    and the command is a thin shell over it. `--verify` fires exactly one
    request per stored row; without it, nothing is built at all.
    """
    from fantabot import apileague
    from fantabot.tokens.errors import TokenError
    from fantabot.tokens.status import orphaned, render_state

    rows = store.status()
    stale = orphaned(rows)
    fingerprint = store.key_fingerprint

    rendered: list[tuple[str, str, str, str]] = []
    for row in rows:
        state = render_state(
            row, now=now, key_fingerprint=fingerprint, is_orphaned=row.league_id in stale
        )
        if verify:
            try:
                apileague.league_status(
                    row.league_id, store=store, transport=transport, now=now
                )
                store.mark_verified(row.league_id, now)
                state = f"{state} · verified"
            except TokenError as exc:
                # Replace rather than append when the local verdict was "ok".
                # Seen on a real run: "ok (357d) · apileague rejected the token"
                # reads as a contradiction. The local check and the server's
                # answer are two different facts, and when they disagree the
                # server's is the one that matters.
                state = f"REJECTED — {exc}" if state.startswith("ok") else f"{state} · {exc}"
        rendered.append(
            (
                str(row.league_id),
                row.league_name or "—",
                f"{row.expires_at:%Y-%m-%d}",
                state,
            )
        )
    return rendered


@app.command()
def token_status(
    league: int = typer.Option(0, "--league", help="Only this lega's row."),
    verify: bool = typer.Option(
        False, "--verify", help="Also call the API once per row to prove the token works."
    ),
) -> None:
    """What is stored, when it expires, and whether it still works.

    Reads only the database, so it works with the browser closed and the site
    down — and because `expires_at` is a plaintext column, it still reports
    expiry with `FANTABOT_ENCRYPTION_KEY` absent. That is the situation where a
    straight answer matters most.
    """
    from datetime import UTC, datetime

    from rich.table import Table
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.config import settings
    from fantabot.db import database_manager
    from fantabot.tokens.crypto import TokenCipher
    from fantabot.tokens.errors import TokenError
    from fantabot.tokens.status import MISSING
    from fantabot.tokens.store import TokenStore

    # No key is not an error here. The whole point of the plaintext expiry
    # columns is that this command still answers without one.
    cipher = None
    if settings.fantabot_encryption_key:
        try:
            cipher = TokenCipher(settings.fantabot_encryption_key)
        except TokenError as exc:
            console.print(f"[yellow]{exc}[/yellow]")
    else:
        console.print(
            "[yellow]FANTABOT_ENCRYPTION_KEY is not set — expiries below are still "
            "accurate; nothing can be decrypted.[/yellow]"
        )

    try:
        with database_manager.get_session() as session:
            rows = token_status_rows(
                TokenStore(session, cipher), now=datetime.now(UTC), verify=verify
            )
    except SQLAlchemyError as exc:
        dsn = make_url(settings.fantabot_database_url).render_as_string(hide_password=True)
        console.print(f"[red]Cannot reach the database at {dsn}[/red]")
        console.print(f"[red]{type(exc).__name__}: {str(exc).splitlines()[0]}[/red]")
        console.print("Start it with: [bold]docker compose up -d[/bold]")
        raise typer.Exit(code=1) from None

    wanted = league or settings.fantabot_league_id
    if wanted:
        rows = [r for r in rows if r[0] == str(wanted)]
        if not rows:
            # A lega is only *known* to exist if you named it or .env did.
            rows = [(str(wanted), "—", "—", MISSING)]

    if not rows:
        console.print("[yellow]No tokens stored — run [bold]fantabot login[/bold].[/yellow]")
        return

    table = Table("lega", "name", "expires", "state")
    for row in rows:
        table.add_row(*row)
    console.print(table)

    if any(MISSING in row[3] or "ORPHANED" in row[3] for row in rows):
        console.print(
            "[dim]ORPHANED = the token is still valid, but a later login did not find "
            "that lega on the account. Nothing is deleted automatically; remove it "
            "with [bold]fantabot token-forget --league <id>[/bold].[/dim]"
        )


@app.command()
def login(
    league: int = typer.Option(0, "--league", help="Only capture this lega."),
    force: bool = typer.Option(False, "--force", help="Re-auth even if the token is valid."),
    verify: bool = typer.Option(
        True, "--verify/--no-verify", help="Confirm each stored token against the API."
    ),
    save_session: bool = typer.Option(
        False, "--save-session", help="Also write data/storage_state.json (default: off)."
    ),
) -> None:
    """Sign in once; store every lega's bearer token encrypted in Postgres.

    Replaces the old `auth` command. You log in yourself in a real browser —
    nothing here scripts a credential, and nothing clicks anything after you do.
    The token is then read from localStorage, encrypted and written to
    `league_tokens`, keyed by lega.

    Running it again when every token is still valid opens no browser at all.
    """
    from fantabot import login as login_module
    from fantabot.tokens.errors import TokenError

    try:
        login_module.run(
            league=league, force=force, verify=verify, save_session=save_session
        )
    except login_module.LoginAborted as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exc.code) from None
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None


@app.command()
def token_forget(
    league: int = typer.Option(0, "--league", help="The lega whose row to remove."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Remove one lega's stored token. Deliberate, one at a time.

    There is no `--all` and no wildcard, on purpose. Removal is manual because a
    `leagues[]` that came back short — a partial load, an API blip — would
    otherwise silently destroy a working token, and re-login is the only
    recovery. Keeping a dead row costs a line of output; deleting a live one
    costs a credential.
    """
    from datetime import UTC, datetime

    from fantabot.db import database_manager
    from fantabot.tokens.status import render_state
    from fantabot.tokens.store import TokenStore

    if not league:
        console.print("[red]--league is required. There is no --all.[/red]")
        raise typer.Exit(code=2)

    with database_manager.get_session() as session:
        store = TokenStore(session)
        row = next((r for r in store.status() if r.league_id == league), None)

        if row is None:
            console.print(
                f"[yellow]No stored token for lega {league} — nothing to remove.[/yellow]"
            )
            return

        # Lega, name and expiry only: never the ciphertext, never the fingerprint.
        state = render_state(row, now=datetime.now(UTC), key_fingerprint=None)
        console.print(f"{row.league_id}  {row.league_name or '—'}  {state}")

        if not yes and not typer.confirm(f"Remove the stored token for lega {league}?"):
            console.print("Nothing removed.")
            return

        store.forget(league)

    console.print(f"[green]Removed the stored token for lega {league}.[/green]")


if __name__ == "__main__":
    app()
