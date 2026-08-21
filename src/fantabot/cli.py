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
