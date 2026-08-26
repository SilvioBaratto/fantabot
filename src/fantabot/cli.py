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
    }
    console.print(settings.model_dump(exclude=secrets))
    console.print(f"stats_source_api_key set: {bool(settings.stats_source_api_key)}")
    console.print(f"lega_password set: {bool(settings.lega_password)}")
    console.print(f"fantabot_encryption_key set: {bool(settings.fantabot_encryption_key)}")

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
    model: str = typer.Option("claude-sonnet-5", help="Model id."),
    season: str = typer.Option("2026/27", help="Which stagione to fetch."),
    lookback_days: int = typer.Option(14, help="Days of news each query should cover."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Show the built prompt."),
    no_run: bool = typer.Option(False, "--no-run", help="Build everything, query nothing."),
) -> None:
    """Fetch weekly news sentiment for the season's quotati players."""
    from fantabot.agentkit.env import strip_dangerous_env
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
    model: str = typer.Option("claude-sonnet-5", help="Model id."),
) -> None:
    """Collect the 11 Mantra schemas and the out-of-position matrix. One-off, not cron."""
    from fantabot.agentkit.env import strip_dangerous_env
    from fantabot.config import settings
    from fantabot.mantra_grid.collect import CollectError, collect
    from fantabot.mantra_grid.writer import COMPAT_FILENAME, SCHEMI_FILENAME, write_json

    strip_dangerous_env()
    console.print("Collecting the 11 Mantra schemas and the compatibility matrix...")
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
                state = f"{state} · {exc}"
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
            rows = token_status_rows(TokenStore(session, cipher), now=datetime.now(UTC))
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

    Replaces `fantabot auth`. You log in yourself in a real browser window —
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


if __name__ == "__main__":
    app()
