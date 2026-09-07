"""`fantabot lega` — pull the lega's own state off the platform, and read it back.

Typer only. The reads and the writes are `application/lega_sync`; this module holds the
two commands, their flags and the tables they print.

`sync` is **dry-run by default**, matching `news fetch`: it does every read and prints
what it found, and writes nothing until `--write`. The reads are all `GET`s against our
own lega, so a dry run is safe to repeat; the flag exists so a first run cannot surprise
anyone with 600 new rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from fantabot.interface.console import console

if TYPE_CHECKING:
    from fantabot.application.lega_sync import SyncResult


def _resolve_league(league: int) -> int:
    from fantabot.config import settings

    league_id = league or settings.fantabot_league_id
    if not league_id:
        console.print("[red]no lega id: pass --league or set FANTABOT_LEAGUE_ID[/red]")
        raise typer.Exit(code=1)
    return league_id


def _print_rosters(result: SyncResult) -> None:
    """One line per team: credits, rosa size, and what the rosa cost.

    `spent` and the cost sum are printed side by side on purpose. They disagreed by
    exactly +/-12 between two teams on 2026-09-02 — a completed trade, visible only
    because both numbers are shown.
    """
    from rich.table import Table

    table = Table(title="rose", header_style="bold")
    for column in ("squadra", "proprietario", "rosa", "speso", "somma costi", "residuo"):
        table.add_column(column, justify="right" if column != "squadra" else "left")
    for team in sorted(result.rosters, key=lambda t: -(t.credits_spent or 0)):
        total = sum(slot.cost for slot in team.roster)
        spent = team.credits_spent or 0
        table.add_row(
            team.nome,
            team.owner,
            str(len(team.roster)),
            str(spent),
            f"[yellow]{total}[/yellow]" if total != spent else str(total),
            str(team.credits_remaining),
        )
    console.print(table)


def _sync(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
    write: bool = typer.Option(False, "--write", help="Persist. Without it, read and print."),
) -> None:
    """Read the whole lega off `apileague.fantacalcio.it` and store it.

    Eight reads: state, roster and lineup settings, every team (rosa and costs included),
    the competitions, each competition's calendar, the custom roles and the lega's player
    list. A read that fails is reported by name and the rest still run — see
    `application/lega_sync` for why partial is the right failure model here.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.league import LeagueRepository
    from fantabot.adapters.tokens.store import TokenStore
    from fantabot.application.lega_sync import collect, persist
    from fantabot.config import settings
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.domain.tokens.errors import TokenError

    league_id = _resolve_league(league)

    try:
        cipher = TokenCipher(settings.fantabot_encryption_key)
        with database_manager.get_session() as session:
            store = TokenStore(session, cipher)
            result = collect(league_id, store=store, reporter=console)
    except TokenError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    if result.rosters:
        _print_rosters(result)
    for failure in result.failures:
        console.print(f"[yellow]failed[/yellow] {failure}")

    if not write:
        console.print("[dim]dry run — nothing written. Re-run with --write.[/dim]")
        raise typer.Exit(code=0 if result.ok else 1)

    try:
        with database_manager.get_session() as session:
            written = persist(result, LeagueRepository(session))
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    for table, count in written.items():
        console.print(f"[green]wrote[/green] {count:>5}  {table}")
    if not result.ok:
        raise typer.Exit(code=1)


def _show(
    league: int = typer.Option(0, "--league", help="Lega id. Defaults to FANTABOT_LEAGUE_ID."),
) -> None:
    """Print the latest stored capture per table. Database only — no network.

    A printer. What "the latest capture" *is* — which tables, how `league_fixture` reaches
    a lega without a `league_id`, and that "never captured" is not the same fact as "zero
    rows" — is `application/lega_reads.capture_inventory`'s to say, because `GET /lega`
    reads the same snapshots and had its own copy of half of it.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from fantabot.adapters.persistence import database_manager
    from fantabot.application.lega_reads import capture_inventory

    league_id = _resolve_league(league)

    from rich.table import Table

    table = Table(title=f"lega {league_id} — ultima cattura", header_style="bold")
    table.add_column("tabella")
    table.add_column("catturata")
    table.add_column("righe", justify="right")

    try:
        with database_manager.get_session() as session:
            rows = capture_inventory(session, league_id)
    except SQLAlchemyError as exc:
        console.print(f"[red]database unreachable: {type(exc).__name__}[/red]")
        raise typer.Exit(code=1) from exc

    for row in rows:
        # `league_fixture` renders an absent timestamp as empty and the five snapshot
        # tables render it as "mai". Both were true before the lift and both stay: the
        # first is a table that upserts and may legitimately hold nothing yet, the second
        # is a capture that never ran.
        if row.table == "league_fixture":
            table.add_row(row.table, str(row.captured_at or ""), str(row.rows))
        else:
            table.add_row(
                row.table,
                str(row.captured_at) if row.captured_at else "[dim]mai[/dim]",
                str(row.rows),
            )

    console.print(table)


def register(app: typer.Typer) -> None:
    """Attach the lega commands to the `lega` group (called from `interface/app`)."""
    app.command("sync")(_sync)
    app.command("show")(_show)
