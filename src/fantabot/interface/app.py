from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.markup import escape

from fantabot.interface.asta import register as register_asta_engine_commands
from fantabot.interface.console import console
from fantabot.interface.harvest import register as register_aste_commands
from fantabot.interface.lega import register as register_lega_commands
from fantabot.interface.lineup import register as register_lineup_commands

if TYPE_CHECKING:
    from datetime import datetime

    import httpx

    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application import (
        pricing as pricing_module,  # annotations only — cli.py must stay import-light
    )
    from fantabot.application.news_fetcher import FetchResult

# `pretty_exceptions_show_locals=False` is load-bearing, not cosmetic: an uncaught
# exception raised while a frame holds `headers = {"Authorization": "Bearer <token>"}`
# (an unmapped httpx/JSON error) would otherwise have Typer's rich handler print that
# frame's locals — the bearer — to stderr and any cron log. Typer's default has been
# `True` in versions the `typer>=0.12` pin allows, so the guarantee is pinned here in code
# rather than left to whichever Typer resolves. See `adapters/http/apileague._send`.
app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)


def _enable_os_trust_store() -> None:
    """Route TLS verification through the operating system's trust store.

    A corporate proxy (Zscaler, on the operator's Windows Enterprise machine)
    re-signs every TLS certificate with a private root that lives in the Windows
    store but not in certifi, so `httpx`'s default verification fails with a bare
    `TransportError` — which `apileague` surfaces as "returned 0", blaming the
    server for a problem that is entirely local. Loading the Windows store into
    OpenSSL by hand does not help either: OpenSSL 3.5 rejects the Zscaler CA
    ("Basic Constraints of CA cert not marked critical") where schannel accepts
    it. `truststore` sidesteps both by delegating verification to the OS, so this
    is a no-op on a machine with no interception and the correct default anywhere.

    Called from the root callback rather than at import time so it runs once per
    real CLI invocation, before any command builds an httpx client, and never as
    a side effect of importing `app` in a test.
    """
    import truststore

    truststore.inject_into_ssl()


@app.callback()
def _main() -> None:
    """Root callback: make every command's TLS verification use the OS trust store."""
    _enable_os_trust_store()

# The five groups. Declared here and nowhere else: every command in the package is
# attached to one of these, so `fantabot --help` is the whole tool and there is one
# place to look for where a command comes from.
#
# `config-check` and `mantra-grid` stay top-level. They are one-offs that belong to no
# family, and a group of one reads as a capability with more behind it than there is.
asta_app = typer.Typer(no_args_is_help=True, help="Plan, watch and bid a Mantra asta.")
harvest_app = typer.Typer(no_args_is_help=True, help="Collect FantaLab auctions and load them.")
db_app = typer.Typer(no_args_is_help=True, help="Database health, scraping and pricing.")
auth_app = typer.Typer(no_args_is_help=True, help="Sign in; manage stored credentials.")
news_app = typer.Typer(no_args_is_help=True, help="Weekly player news sentiment.")
lineup_app = typer.Typer(
    no_args_is_help=True, help="Read, plan and submit the weekly Mantra formazione."
)
lega_app = typer.Typer(
    no_args_is_help=True, help="Pull the lega's own state off the platform, and read it back."
)


@app.command()
def config_check() -> None:
    """Print resolved settings (secrets masked) — sanity check before running anything.

    A printer. Which fields are secret, and how the DSN renders, are
    `application/config_report.py`'s — the app's System page shows the same report, and a
    second copy of "which fields are secret" is a copy that drifts.

    Cron captures stdout, so every line here outlives the run in a log file.
    """
    from fantabot.application.config_report import build_report

    report = build_report()
    console.print(dict(report.settings))
    for name, is_set in report.secrets_set.items():
        console.print(f"{name} set: {is_set}")
    # Printed in full, unlike the token beside it: routing, not a credential, and an
    # unexpected value here is the fastest explanation for a cron run that went
    # somewhere other than the subscription.
    agent_base_url = report.settings.get("fantabot_agent_base_url")
    console.print(f"fantabot_agent_base_url: {agent_base_url or '(subscription)'}")

    if report.database_url_error is not None:
        # Loud, and non-zero, rather than deferred to the first connect — where it would
        # read as a database being down instead of as a `.env` to edit.
        console.print(f"fantabot_database_url: INVALID — {report.database_url_error}")
        raise typer.Exit(1)
    # `soft_wrap`: Rich hard-wraps at the console width, and at 40 columns this came back
    # as three fragments split mid-token. This is the line an operator copies into
    # `alembic.ini` or a `psql` invocation, so it has to survive in one piece.
    console.print(f"fantabot_database_url: {report.database_url}", soft_wrap=True)


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

    from fantabot.adapters.agent.env import strip_dangerous_env
    from fantabot.adapters.persistence.news_pool import load_pool
    from fantabot.application.news_fetcher import Progress, fetch_all, format_cost_line
    from fantabot.config import settings
    from fantabot.domain.news.pool import PoolPlayer
    from fantabot.domain.news.prompt import build_prompt
    from fantabot.domain.news.sink import SentimentSink

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

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.sentiment import SentimentRepository

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
    from fantabot.adapters.agent.env import strip_dangerous_env
    from fantabot.adapters.files.mantra_writer import write_json
    from fantabot.application.mantra_collector import CollectError, collect
    from fantabot.config import settings
    from fantabot.domain.shared.resources import COMPAT_FILENAME, SCHEMI_FILENAME, data_dir

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

    # Written where `legality.load_compat` reads, which is inside the package. These
    # two files are the matcher's input, not runtime state; writing them to
    # `settings.fantabot_data_dir` meant the reader and the writer agreed only when
    # the process happened to start in the repository root.
    out = data_dir()
    write_json(out / SCHEMI_FILENAME, result.grid)
    write_json(out / COMPAT_FILENAME, result.matrix)
    console.print(f"[green]-> {out / SCHEMI_FILENAME}[/green]")
    console.print(f"[green]-> {out / COMPAT_FILENAME}[/green]")
    console.print(
        "[yellow]Verify both by hand against rules/sistema-mantra.md before committing.[/yellow]"
    )



def db_backfill_teams() -> None:
    """Resolve club codes to full names for a season whose fixtures arrived late.

    The scrapers print an instruction to run this when they meet a season whose
    ``teams`` rows have codes but no full names — `voti` carries the full names and
    is scraped separately, so a quotazioni-first run legitimately has the gap.

    It existed as ``python scripts/_db.py backfill-team-names`` and was named in that
    printed remedy, which is the only reason anyone would ever have found it. The file
    moved into the package, so the instruction pointed at a path that no longer
    existed; an operator-facing remedy has to name a command that does.

    A printer over `application/team_maintenance.backfill_teams`, so the Synchronize
    page refuses the same backfill with the same sentence. This body used to catch
    `SQLAlchemyError` alone, which left `TeamMappingError` — the one refusal a backfill
    actually has — reaching the terminal as a traceback.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.application.team_maintenance import NamesUnresolved, backfill_teams

    try:
        with database_manager.get_session() as session:
            changed = backfill_teams(session)
    except NamesUnresolved as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"resolved {changed} club name(s)")


def db_snapshot_team(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
) -> None:
    """Capture our own team's credits and roster ids into `league_team_snapshot`.

    The one network call is `apileague.my_team` (`GET /onboarding/v1/league/teams/my`),
    authenticated with the token already stored for `league` — nothing here logs in or
    touches a browser. Every call inserts a **new** row; `league_team_snapshot` is
    append-only, so a rescan never overwrites the last capture (`LeagueRepository`'s own
    docstring). The response's credits and roster ids are not secrets and are printed;
    the bearer token used to fetch them never is.

    A printer over `application/team_maintenance.snapshot_team`. Which endpoint, which
    parser and which repository method used to be chosen here, where the Synchronize
    page could not reach them — three choices a second surface would have had to guess.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.team_maintenance import snapshot_team
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = league or settings.fantabot_league_id
    if not league_id:
        console.print("[red]no lega id: pass --league or set FANTABOT_LEAGUE_ID[/red]")
        raise typer.Exit(code=1)

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            snapshot = snapshot_team(session, league_id, store=TokenStore(session, cipher))
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    spent = snapshot.credits_spent or 0
    initial = snapshot.credits_initial or 0
    remaining = snapshot.credits_remaining
    console.print(
        f"[green]saved[/green] {snapshot.nome!r} (team {snapshot.team_id}) — "
        f"{spent}/{initial} credits spent, {remaining} left"
    )


def db_scrape(
    table: str = typer.Argument(
        ..., help="quotazioni | statistiche | voti — which pages to fetch."
    ),
    seasons: list[str] = typer.Option(
        [], "--season", help="Repeatable. Defaults to every season the scraper knows."
    ),
) -> None:
    """Fetch from fantacalcio.it and upsert. Reads a live site, so it is slow and polite.

    `voti` is roughly 38 GETs per season at one second apart, and writes both
    match-grain tables. Run `quotazioni` first on a fresh database: `players` and
    `teams` have no outbound foreign keys and everything else points at them, so
    writing the facts first is a foreign-key violation rather than a slow run.

    A printer over `application/scrape` since T23: which tables are scrapable, which
    module each one is, and what an omitted `--season` resolves to moved there so the
    app's form offers the same three and refuses the same seasons with the same
    sentences, rather than inventing a second idea of a scrapable table.
    """
    # Imported inside the body, like every other command that touches the database:
    # `tests/test_db_boundary.py` asserts that importing the CLI loads neither
    # sqlalchemy nor playwright, and these modules pull in the whole persistence stack.
    from fantabot.application.scrape import (
        InvalidScrape,
        clean_scrape,
        current_season,
        run_scrape,
        scrapables,
    )

    try:
        request = clean_scrape(table, seasons)
    except InvalidScrape as refused:
        console.print(f"[red]{refused}[/red]")
        raise typer.Exit(2) from None

    # Said before the first request, not after the last: a `voti` run is minutes per
    # season, and "which seasons is this actually fetching" is not a question worth
    # waiting out. It is also the whole of the stale-default trap made visible — the run
    # below reports success either way.
    console.print(f"scraping {request.table}: {', '.join(request.seasons)}")
    if not seasons:
        now = current_season(date.today())
        asked = next(s for s in scrapables(now) if s.table == request.table)
        if asked.default_is_stale:
            console.print(
                f"[yellow]{request.table}'s default stops before {now}[/yellow] — this run "
                f"will not touch the season being played. Use `--season {now}`."
            )

    run_scrape(request)


def db_price(
    system: str = typer.Option("classic", help="classic | mantra — which listone to price."),
    top_n: int = typer.Option(15, "--top-n", help="How many rows to show per table."),
) -> None:
    """Fit the target-price model and upsert `target_price`.

    The only numbers in this repo that get spent as real credits. It reads
    `quotazioni`, `statistiche` and `qi_bias`, so run the scrapers first.
    """
    if system not in ("classic", "mantra"):
        raise typer.BadParameter(f"{system!r} is not a listone. Pick classic or mantra.")

    from fantabot.application import pricing as pricing

    render_pricing(pricing.run(system=system, top_n=top_n), top_n)


def render_pricing(report: pricing_module.PricingReport, top_n: int) -> None:
    """Print a pricing run. The only part of `db price` that knows what a terminal is.

    It lived inside `pricing.run`, which is why the application layer imported
    `rich.table` -- and why the layer test carried an expected violation for it. The
    numbers are the same; where they are formatted is not.
    """
    import math

    from rich.table import Table

    console.print(
        f"[bold]{report.system}: fitted role fades "
        "(log(qa/qi) ~ prior_media_fantavoto, OLS):[/bold]"
    )
    fades = Table()
    for column, justify in (("macro role", "left"), ("n", "right"), ("slope", "right"),
                            ("intercept", "right"), ("clamp range (as %)", "right")):
        fades.add_column(column, justify=justify)  # type: ignore[arg-type]
    for summary in report.fades:
        low = (math.exp(summary.fade.clamp_lo) - 1.0) * 100.0
        high = (math.exp(summary.fade.clamp_hi) - 1.0) * 100.0
        fades.add_row(
            summary.role,
            str(summary.observations),
            f"{summary.fade.slope:+.3f}",
            f"{summary.fade.intercept:+.3f}",
            f"[{low:+.0f}%, {high:+.0f}%]",
        )
    console.print(fades)

    console.print(f"\n[bold]Team discount factors applied:[/bold] {report.team_factors}\n")
    console.print(f"wrote {report.stored} target_price rows for {report.system}\n")

    for heading, rows in (
        (f"Top {top_n} biggest UPWARD adjustments (target > qi):", report.biggest_bumps),
        (f"\nTop {top_n} biggest DOWNWARD adjustments (target < qi):", report.biggest_cuts),
    ):
        console.print(heading, markup=False)
        for row in rows:
            console.print(
                f"  {row.nome:20s} {row.squadra:4s} {row.role:8s}({row.macro_role:7s}) "
                f"qi={row.qi:>3d} -> target={row.target_price:>3d}  flags={row.flags}",
                markup=False,
            )

    console.print(f"\nFlag counts: {report.flag_counts}")


def db_exclude(
    player: int = typer.Option(..., help="Fantacalcio player id to keep out of every plan."),
    reason: str = typer.Option(..., help="What happened. Say it in words, with a date."),
    source: str = typer.Option("", help="Where the claim came from — a URL, a report."),
) -> None:
    """Keep a player out of every asta plan.

    For a player the listone still carries but who cannot be bought — a transfer out of
    Serie A, most often. Nothing else in the engine can do this: the scraper reproduces
    the site, and the sentiment gate is floored so news tilts a value and never vetoes
    it. See `adapters/persistence/models/exclusions.py`.

    A printer. What a valid exclusion is, and how a name is resolved, are
    `application/exclusions.py`'s — the Asta page records them through the same
    function, and a second copy of "a reason may not be blank" is a copy that drifts.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.exclusions import InvalidExclusion, record_exclusion

    try:
        with database_manager.get_session() as session:
            recorded = record_exclusion(session, player, reason=reason, source=source)
            session.commit()
    except InvalidExclusion as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None

    who = f"{recorded.row.player_id} {recorded.row.nome}" if recorded.row.nome else (
        str(recorded.row.player_id)
    )
    console.print(f"[green]excluded {who}[/green]: {recorded.row.reason}")
    if recorded.row.nome is None:
        # The typo case, said out loud. An id that resolves to nothing is the one way
        # this command silently does nothing at all, and the operator who typed it is
        # the only person who will ever be in a position to notice.
        console.print(
            f"[yellow]no player with id {recorded.row.player_id} has been scraped[/yellow] — "
            "check the id, or scrape the season it belongs to"
        )
    console.print(f"[dim]{recorded.total} exclusions in total[/dim]")


def db_exclusions() -> None:
    """List the players kept out of every plan, and why.

    A printer over `application/exclusions.read_exclusions`. The name join used to be a
    raw `SELECT ... WHERE id = ANY(:ids)` in this body, where the Asta page could not
    reach it.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.exclusions import read_exclusions

    with database_manager.get_session() as session:
        rows = read_exclusions(session)

    if not rows:
        console.print("[dim]no exclusions — every player on the listone is buyable[/dim]")
        return
    for row in rows:
        # `(not scraped)` rather than `?`, which this printed for both the unknown name
        # and the absent id. They are different facts with different remedies.
        console.print(f"  {row.player_id:<7} {row.nome or '(not scraped)':<20} {row.reason}")
        if row.source:
            console.print(f"  {'':<7} {'':<20} [dim]{row.source}[/dim]")


def db_unexclude(
    player: int = typer.Option(..., help="Fantacalcio player id to let back into the plan."),
) -> None:
    """Withdraw an exclusion, putting the player back into every plan.

    The remedy for a typo'd id, which until this existed was `psql` and nothing else.
    A printer over `application/exclusions.remove_exclusion`, so the Asta page's remove
    control refuses the same removals with the same sentence.

    It prints the row it removed. The reason is the only half nothing else in the
    database holds, so printing it is what makes the removal undoable by hand.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.application.exclusions import ExclusionNotFound, remove_exclusion

    try:
        with database_manager.get_session() as session:
            removed = remove_exclusion(session, player)
            session.commit()
    except ExclusionNotFound as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None

    who = f"{removed.row.player_id} {removed.row.nome}" if removed.row.nome else (
        str(removed.row.player_id)
    )
    console.print(f"[green]removed {who}[/green]: {removed.row.reason}")
    if removed.row.source:
        console.print(f"[dim]{removed.row.source}[/dim]")
    console.print(f"[dim]{removed.total} exclusions in total[/dim]")


def _pg_dump_argv(database_url: str) -> list[str]:
    """`application/db_dump.pg_dump_argv`, under the name this module's tests know it by.

    Kept as an alias rather than deleted: `tests/interface/test_cli_db_dump.py` pins the
    libpq translation against *this* name, and a lift is verified by the command's own
    tests passing unchanged. Editing them to follow the function would have made the
    proof circular.
    """
    from fantabot.application.db_dump import pg_dump_argv

    return pg_dump_argv(database_url)


def db_dump() -> None:
    """Dump the database to a timestamped file OUTSIDE the repository.

    One local server holds everything, and that is the entire durability story: losing
    `~/.fantabot/pgdata` destroys both 50,634-row match-grain tables, and re-scraping
    them is roughly 750 GETs per season against a site under no obligation to keep
    serving 2022/23.

    The dump lands in `$HOME`, never under the repo: it contains the `league_tokens`
    rows — encrypted, but still credentials — and anything inside the working tree is
    one `git add -A` from a commit. `$HOME` is also a different volume from the
    external drive, so it survives unmounting.

    Custom format (`-Fc`), which is what makes `pg_restore` usable and selective.
    To restore into a scratch database::

        fantabot-app db create fantabot_restore
        pg_restore -d "$(fantabot-app db url --database fantabot_restore)" \\
          ~/fantabot-db-YYYYMMDD.dump
        FANTABOT_DATABASE_URL="$(fantabot-app db url --database fantabot_restore)" \\
          fantabot db check

    After any restore, realign the identity sequences: `COPY` and `pg_restore` write
    explicit keys without advancing the sequence that owns them, so the next insert
    repeats one already present (measured 2026-09-05: `asta.key` at 20 against a table
    maximum of 5,707). Row counts prove the data arrived, not that it can be written to.
    """
    from datetime import UTC, datetime

    from fantabot.application.db_dump import (
        DumpRefused,
        PgDumpFailed,
        PgDumpMissing,
        dump_target,
        run_dump,
    )
    from fantabot.config import settings

    # UTC rather than the local date `db scrape` reads: the filename is the only thing
    # that distinguishes two dumps, and a machine that travels would otherwise write
    # today's dump over yesterday's.
    try:
        out = dump_target(Path.home(), datetime.now(UTC).date())
    except DumpRefused as refused:
        console.print(f"[red]{refused}[/red]")
        raise typer.Exit(code=1) from None

    try:
        wrote = run_dump(out, settings.fantabot_database_url)
    except (PgDumpMissing, PgDumpFailed) as failed:
        console.print(f"[red]{failed}[/red]")
        raise typer.Exit(code=1) from None

    console.print(f"wrote {wrote.path} ({wrote.size_bytes / 1_048_576:.0f} MB)")
    console.print("[dim]restore: see `fantabot db dump --help`[/dim]")


def db_check() -> None:
    """Database health, plus a row count and on-disk size for every table."""
    from rich.table import Table
    from sqlalchemy.exc import SQLAlchemyError

    import fantabot.adapters.persistence.models  # noqa: F401  -- registers every table on Base.metadata
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.admin import AdminRepository
    from fantabot.config import settings

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
        console.print("Start it with: [bold]fantabot-app db start[/bold]")
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
    from fantabot.adapters.http import apileague as apileague
    from fantabot.domain.tokens.errors import TokenError
    from fantabot.domain.tokens.status import orphaned, render_state

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

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError
    from fantabot.domain.tokens.status import MISSING

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
        console.print("Start it with: [bold]fantabot-app db start[/bold]")
        raise typer.Exit(code=1) from None

    wanted = league or settings.fantabot_league_id
    if wanted:
        rows = [r for r in rows if r[0] == str(wanted)]
        if not rows:
            # A lega is only *known* to exist if you named it or .env did.
            rows = [(str(wanted), "—", "—", MISSING)]

    if not rows:
        console.print("[yellow]No tokens stored — run [bold]fantabot auth login[/bold].[/yellow]")
        return

    table = Table("lega", "name", "expires", "state")
    for row in rows:
        table.add_row(*row)
    console.print(table)

    if any(MISSING in row[3] or "ORPHANED" in row[3] for row in rows):
        console.print(
            "[dim]ORPHANED = the token is still valid, but a later login did not find "
            "that lega on the account. Nothing is deleted automatically; remove it "
            "with [bold]fantabot auth forget --league <id>[/bold].[/dim]"
        )


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
    from fantabot.adapters.browser.capture import read_storage_state, real_browser
    from fantabot.application import auth_login as login_module
    from fantabot.application.login_wait import CaptureUnreadable
    from fantabot.domain.tokens.errors import SignInWindowClosed, TokenError

    try:
        login_module.run(
            browser_factory=real_browser,
            read_state=read_storage_state,
            league=league,
            force=force,
            verify=verify,
            save_session=save_session,
            report=console,
        )
    except login_module.LoginAborted as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=exc.code) from None
    except (SignInWindowClosed, CaptureUnreadable) as exc:
        # Reported apart from TokenError so the message names what the human did,
        # rather than "no leghe found in the browser session".
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None


def token_forget(
    league: int = typer.Option(0, "--league", help="The lega whose row to remove."),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt."),
) -> None:
    """Remove one lega's stored token: that row, and nothing else.

    Deliberate, one at a time. There is no `--all` and no wildcard, on purpose.
    Removal is manual because a `leagues[]` that came back short — a partial
    load, an API blip — would otherwise silently destroy a working token, and
    re-login is the only recovery. Keeping a dead row costs a line of output;
    deleting a live one costs a credential.

    The app's Disconnect button is not this command. It calls DELETE
    /auth/league/{id}, which removes the token and then purges the lega across
    six tables: league_snapshot, league_team_snapshot, league_player_pool,
    league_custom_role, league_competition and league_fixture. Two similar names
    for two different acts — this one leaves all six standing, and a re-login
    undoes it.
    """
    from datetime import UTC, datetime

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.domain.tokens.status import render_state

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
# One registration block, and the only one. Names are explicit because the group
# supplies the prefix — `db check`, not `db db check`.
for _group, _name in (
    (asta_app, "asta"),
    (harvest_app, "harvest"),
    (db_app, "db"),
    (auth_app, "auth"),
    (news_app, "news"),
    (lineup_app, "lineup"),
    (lega_app, "lega"),
):
    app.add_typer(_group, name=_name)

news_app.command("fetch")(news_fetch)
db_app.command("check")(db_check)
db_app.command("backfill-teams")(db_backfill_teams)
db_app.command("snapshot-team")(db_snapshot_team)
db_app.command("scrape")(db_scrape)
db_app.command("price")(db_price)
db_app.command("exclude")(db_exclude)
db_app.command("exclusions")(db_exclusions)
db_app.command("unexclude")(db_unexclude)
db_app.command("dump")(db_dump)
auth_app.command("login")(login)
auth_app.command("status")(token_status)
auth_app.command("forget")(token_forget)

register_aste_commands(harvest_app, auth_app)
register_asta_engine_commands(asta_app)
register_lineup_commands(lineup_app)
register_lega_commands(lega_app)


if __name__ == "__main__":
    app()
