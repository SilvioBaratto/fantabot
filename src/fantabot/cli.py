import asyncio
from datetime import date
from pathlib import Path

import typer
from rich.console import Console

from fantabot import auth as auth_module

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def auth() -> None:
    """One-time interactive login — opens a real browser window, saves the session."""
    auth_module.run()


@app.command()
def config_check() -> None:
    """Print resolved settings (with the API key masked) — sanity check before running anything."""
    from fantabot.config import settings

    console.print(settings.model_dump(exclude={"stats_source_api_key"}))
    console.print(f"stats_source_api_key set: {bool(settings.stats_source_api_key)}")


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


if __name__ == "__main__":
    app()


@app.command()
def news_fetch(
    scope: str = typer.Option("pool", help="Only 'pool' is implemented — see below."),
    write: bool = typer.Option(
        False, "--write", help="Append to the CSV. Off = query and discard."
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
    from fantabot.config import settings
    from fantabot.news.pipeline import fetch_all
    from fantabot.news.pool import load_pool
    from fantabot.news.prompt import build_prompt
    from fantabot.news.store import append_rows, existing_keys

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

    data_dir: Path = settings.fantabot_data_dir
    players = load_pool(
        data_dir / "quotazioni_classic.csv", data_dir / "quotazioni_mantra.csv", season=season
    )
    if only:
        players = [p for p in players if only.lower() in p.nome.lower()]
    today = date.today()
    out_path = data_dir / f"player_sentiment_{season.replace('/', '-')}.csv"

    if not force:
        seen = existing_keys(out_path)
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
        append_rows(out_path, result.rows)
        console.print(f"[green]{len(result.rows)} rows -> {out_path}[/green]")
    else:
        for row in result.rows:
            console.print(row)
        console.print(
            f"[dim]{len(result.rows)} rows discarded (--write not given), "
            f"{len(result.failures)} failures.[/dim]"
        )
