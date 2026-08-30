from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console
from rich.markup import escape

from fantabot.asta_engine.cli import register as register_asta_engine_commands
from fantabot.aste.cli import register as register_aste_commands

if TYPE_CHECKING:  # annotations only — cli.py must stay import-light
    from datetime import datetime

    import httpx

    from fantabot.news.pipeline import FetchResult
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


def _report_stop(result: FetchResult) -> None:
    """Say that the run ended early, and exit non-zero so cron hears it.

    Reported after the readings are stored, never instead of storing them: the
    queries that did succeed are the expensive part and must land first.

    The message names the count and the last reason because the reason alone is
    useless — `agent returned no structured output` is what a single confused
    player produces *and* what an exhausted quota produces, and only the count
    tells them apart. Measured 2026-08-28: fifteen of those in a row were an
    Ollama 429, and the run was on course to spend 458 more queries on it.
    """
    if not result.stopped_early:
        return
    console.print(f"[red]{result.stopped_early}[/red]")
    console.print(
        f"{result.skipped} player(s) were not queried. This is a backend problem, not a "
        "player one — check it, then re-run: the readings already stored are skipped."
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
    flush_every: int = typer.Option(
        5, help="Store readings every N completions, so a crash costs at most N."
    ),
    max_consecutive_failures: int = typer.Option(
        10, help="Stop after N failures in a row with no success between. 0 = never stop."
    ),
    model: str = typer.Option("", help="Model id. Empty = FANTABOT_AGENT_MODEL."),
    season: str = typer.Option("2026/27", help="Which stagione to fetch."),
    run_day: str = typer.Option(
        "", "--date", help="Run day, YYYY-MM-DD. Empty = today. Pin it to resume a run."
    ),
    lookback_days: int = typer.Option(14, help="Days of news each query should cover."),
    print_prompt: bool = typer.Option(False, "--print-prompt", help="Show the built prompt."),
    no_run: bool = typer.Option(False, "--no-run", help="Build everything, query nothing."),
) -> None:
    """Fetch weekly news sentiment for the season's quotati players."""
    import signal
    import time

    from fantabot.agentkit.env import strip_dangerous_env
    from fantabot.config import settings
    from fantabot.news.pipeline import Progress, fetch_all, format_cost_line
    from fantabot.news.pool import PoolPlayer, load_pool
    from fantabot.news.prompt import build_prompt
    from fantabot.news.sink import SentimentSink

    if scope != "pool":
        # Not deferred-and-half-built: reading a roster needs the league API
        # (docs/leghe-api.md has the endpoints), and with two leagues it would
        # also need a --league selector. Falling back to the full pool would
        # spend 523 queries and look like it worked.
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

    # Checked before anything is spent, the way `login` checks everything it can
    # before opening a browser. This one value keys both halves of resume — the
    # filter `existing_keys(today)` and the stored `data_run` — so getting it
    # from the clock means a run that crosses midnight silently starts a new
    # week and re-queries the pool it had already half collected.
    today = date.today()
    if run_day:
        try:
            today = date.fromisoformat(run_day)
        except ValueError:
            console.print(
                f"[red]{escape(run_day)!r} is not a date.[/red] Use YYYY-MM-DD. "
                "Falling back to today would spend the queries under a key you did not ask for."
            )
            raise typer.Exit(code=2) from None
        if today > date.today():
            # A typo in the year would write a week nothing collected, and the
            # reader takes the most recent row per player.
            console.print(
                f"[red]{escape(run_day)} has not happened yet.[/red] "
                "A reading is dated by the day it describes."
            )
            raise typer.Exit(code=2)

    # One session for both reads: the pool and the resume filter are the same
    # question asked twice — what is this run going to query?
    with database_manager.get_session() as session:
        players = load_pool(session, season)
        seen = set() if force else SentimentRepository(session).existing_keys(today)

    if only:
        players = [p for p in players if only.lower() in p.nome.lower()]
    candidates = len(players)
    if not force:
        players = [p for p in players if (today.isoformat(), p.id) not in seen]
    # Said out loud below. The resume filter has always existed; until readings
    # were stored as they landed there was never anything for it to skip, so it
    # had no visible effect and no way to be trusted after a crash.
    resumed = candidates - len(players)
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
    if resumed:
        console.print(
            f"[dim]resuming: {resumed} of {candidates} already stored for {today}[/dim]"
        )
    console.print(f"Querying {len(players)} players at concurrency {concurrency}...")

    # Storing as they land, not all at the end. 548 players at two a minute is
    # nearly two hours, and a single upsert after the last one made every minute
    # of that all-or-nothing — with the resume filter unable to help, because
    # nothing had been stored for it to resume from.
    sink: SentimentSink | None = None
    if write:

        def flush(rows: list[dict[str, str]]) -> int:
            with database_manager.get_session() as session:
                return SentimentRepository(session).upsert_rows(rows, force=force)

        def on_flush_error(exc: Exception) -> None:
            console.print(
                f"[red]storing failed: {type(exc).__name__}: {escape(str(exc))}[/red]\n"
                "The readings are held and retried on the next completion; "
                "collection is unaffected."
            )

        sink = SentimentSink(flush, every=flush_every, on_error=on_flush_error)

    started = time.monotonic()

    def on_start(player: PoolPlayer) -> None:
        console.print(f"[dim]-> {escape(player.nome)}[/dim]")

    def on_result(progress: Progress) -> None:
        """One line per finished player. No square brackets: Rich reads them as
        markup, and `12/548` is not a style."""
        elapsed = time.monotonic() - started
        rate = progress.done / elapsed if elapsed > 0 else 0.0
        left = (progress.total - progress.done) / rate if rate > 0 else 0.0
        head = f"{progress.done:>4}/{progress.total}"
        outcome = progress.outcome
        if outcome.row is None:
            # Escaped: the reason is agent-written text. Rich reads `[type=...,
            # input_value=...]` — the tail of every pydantic rejection — as a
            # style and deletes it, and raises MarkupError on a `[/...]`, which
            # inside a gathered coroutine ends the whole run.
            console.print(
                f"[yellow]{head} {escape(outcome.player.nome)}: "
                f"{escape(outcome.failure or '')}[/yellow]"
            )
            return
        row = outcome.row
        note = ""
        if sink is not None:
            sink.add(row)
            note = f" · {sink.stored} stored"
        console.print(
            f"{head} {escape(outcome.player.nome)} · sentiment {row['sentiment']} "
            f"conf {row['confidenza']} · {row['n_fonti']} fonti{note} · ~{left / 60:.0f}m left"
        )

    # Ctrl-C was ignored: two SIGINTs thirty seconds apart did nothing, and the
    # SIGTERM that followed skipped the drain below — the path written for
    # exactly this — taking four fetched readings with it. The fix is not to
    # cancel: a query that has already spent its web searches should finish and
    # be stored. What must stop is asking for more, which is what the fan-out's
    # own `should_stop` does. A second Ctrl-C restores the default and hurts.
    stop_requested: list[bool] = []
    previous_sigint = signal.getsignal(signal.SIGINT)
    installed = False

    def _request_stop(_signum: int, _frame: Any) -> None:
        if stop_requested:
            signal.signal(signal.SIGINT, previous_sigint)
            raise KeyboardInterrupt
        stop_requested.append(True)
        console.print(
            "[yellow]interrupt — finishing the queries in flight, then storing them. "
            "Ctrl-C again to stop now.[/yellow]"
        )

    try:
        signal.signal(signal.SIGINT, _request_stop)
        installed = True
    except ValueError:
        # Not the main thread. The run is fine; only the graceful stop is not
        # available, and saying nothing beats refusing to collect.
        pass

    try:
        result = asyncio.run(
            fetch_all(
                players,
                concurrency=concurrency,
                lookback_days=lookback_days,
                today=today,
                model=model,
                stagione=season,
                on_start=on_start,
                on_result=on_result,
                max_consecutive_failures=max_consecutive_failures,
                should_stop=lambda: bool(stop_requested),
            )
        )
    except BaseException:
        # Ctrl-C, or anything that escaped a coroutine. Everything below is
        # skipped on this path, the final drain included — so readings already
        # fetched and queued would be discarded at the moment they cost most.
        if sink is not None and sink.pending:
            saved = sink.drain()
            console.print(f"[yellow]interrupted — {saved} row(s) saved on the way out[/yellow]")
        raise
    finally:
        if installed:
            signal.signal(signal.SIGINT, previous_sigint)

    for name, reason in result.failures:
        console.print(f"[yellow]failed[/yellow] {escape(name)}: {escape(reason)}")
    if result.rate_limited:
        console.print("[yellow]rate limits were hit; the run backed off and continued[/yellow]")

    # Token spend and cache reuse for the whole run. No brackets in the line, so it
    # is Rich-markup-safe without escaping. The cache-read % is what the caching work
    # is meant to move; the dollar figure is hedged (0 on a custom model id).
    console.print(f"[dim]{format_cost_line(result.usage)}[/dim]")

    if sink is not None:
        # The end-of-run pass stays, and is normally a no-op: the sink skips keys
        # it has already taken. It is the guarantee of completeness, so that a
        # bug in the incremental path cannot lose a run — belt as well as braces.
        # force means both "re-query him" and "overwrite what is stored": without
        # it a same-day re-run is a no-op rather than a duplicate.
        sink.extend(result.rows)
        sink.drain()
        console.print(f"[green]{sink.stored} rows -> player_sentiment[/green]")
        if sink.flush_failures:
            console.print(
                f"[yellow]{sink.flush_failures} flush(es) failed and were retried[/yellow]"
            )
        if sink.pending:
            # Non-zero exit: the queries are spent and these readings are not on
            # disk. Silence here would report the week as collected.
            console.print(
                f"[red]{sink.pending} row(s) could not be stored. "
                "Fix the database and re-run — the rest is already saved.[/red]"
            )
            raise typer.Exit(code=1)
        _report_stop(result)
    else:
        for row in result.rows:
            console.print(row)
        console.print(
            f"[dim]{len(result.rows)} rows discarded (--write not given), "
            f"{len(result.failures)} failures.[/dim]"
        )
        _report_stop(result)


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


# Last registration, so the five harvest commands list together at the end of
# `--help` rather than in the middle. Above the guard, not below it: Typer sees
# only the commands registered by the time `app()` runs, and a registration
# under the guard would give `python cli.py` a shorter menu than `fantabot` —
# which is the split test_cli_entrypoints.py exists to refuse.
register_aste_commands(app)
register_asta_engine_commands(app)


if __name__ == "__main__":
    app()
