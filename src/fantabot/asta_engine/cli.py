"""The offline asta commands: `asta-optimize` and `asta-legality`. Read-only, no FantaLab.

The thin I/O shell: fetch the Mantra pool, values and prices from Postgres, hand them to the
pure engine (legality / value / optimizer / report), and print. Registered on the root app
by ``register(app)``, mirroring ``aste/cli.py``.
"""

from __future__ import annotations

import typer
from rich.console import Console

from .legality import build_legality, fieldable_schemi, load_compat
from .live import normalize
from .opponents import format_advisory, format_opponents, track_opponents
from .optimizer import InfeasibleRoster, optimize_roster
from .prices import expected_prices
from .report import build_pool, build_value, format_legality, format_roster, parse_ids
from .reservation import rolling_advisory
from .state import AstaState

console = Console()

_SEASON = "2026/27"


def asta_optimize(
    owned: str = typer.Option("", help="Player ids already owned, comma/space separated."),
    budget: float = typer.Option(500.0, help="Remaining credits to spend."),
    lam: float = typer.Option(0.0, "--lam", help="Risk aversion; higher diversifies across clubs."),
    fallbacks: int = typer.Option(3, help="How many next-best plans to show."),
    season: str = typer.Option(_SEASON, help="Which stagione's Mantra listone."),
) -> None:
    """Print the current optimal 30-man Mantra roster and next-best plans. Read-only."""
    from fantabot.db import database_manager
    from fantabot.db.repositories.reference import ReferenceRepository

    with database_manager.get_session() as session:
        quotazioni = ReferenceRepository(session).quotazioni(season, "mantra")
        prices = expected_prices(session)

    pool = build_pool({pid: row.ruoli_codice for pid, row in quotazioni.items()})
    teams = {pid: row.squadra for pid, row in quotazioni.items()}
    names = {pid: row.nome for pid, row in quotazioni.items()}
    value = build_value({pid: row.fvm for pid, row in quotazioni.items()}, priced_ids=set(prices))
    legality = build_legality(load_compat())
    state = AstaState(owned=parse_ids(owned), total_budget=budget)

    try:
        result = optimize_roster(
            state,
            pool,
            value=value,
            prices=prices,
            teams=teams,
            legality=legality,
            lam=lam,
            n_fallbacks=fallbacks,
        )
    except InfeasibleRoster as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(format_roster(result.optimal, names, prices))
    for index, fallback in enumerate(result.fallbacks, start=1):
        console.print(
            f"[dim]fallback {index}: cost {fallback.total_cost:.0f} | obj {fallback.objective:.1f}[/dim]"
        )


def asta_legality(
    rosa: str = typer.Option(..., help="Player ids in the rosa, comma/space separated."),
    season: str = typer.Option(_SEASON, help="Which stagione's Mantra listone."),
) -> None:
    """Print which of the 11 Mantra schemi a rosa can field. Read-only."""
    from fantabot.db import database_manager
    from fantabot.db.repositories.reference import ReferenceRepository

    with database_manager.get_session() as session:
        quotazioni = ReferenceRepository(session).quotazioni(season, "mantra")

    ids = parse_ids(rosa)
    pool = build_pool({pid: quotazioni[pid].ruoli_codice for pid in ids if pid in quotazioni})
    schemi = fieldable_schemi(pool, build_legality(load_compat()))
    console.print(format_legality(schemi))


def asta_live(
    replay: str = typer.Option(..., help="Path to a JSONL of raw room states (a captured room)."),
    team: str = typer.Option(..., help="Our team id in the room."),
    budget: float = typer.Option(500.0, help="Our starting credits."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    season: str = typer.Option(_SEASON, help="Which stagione's Mantra listone."),
) -> None:
    """Replay a captured room and render the rolling advisory. Read-only.

    A stand-in for the live socket (the own-room feed is still an open question): it drives the
    exact same engine off ``AssignmentEvent`` as a live room would, from a captured file.
    """
    import json
    from pathlib import Path

    from fantabot.db import database_manager
    from fantabot.db.repositories.reference import ReferenceRepository

    rows = [
        json.loads(line)
        for line in Path(replay).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = normalize(row.get("state", row) for row in rows)

    with database_manager.get_session() as session:
        quotazioni = ReferenceRepository(session).quotazioni(season, "mantra")
        prices = expected_prices(session)

    pool = build_pool({pid: row.ruoli_codice for pid, row in quotazioni.items()})
    teams = {pid: row.squadra for pid, row in quotazioni.items()}
    names = {pid: row.nome for pid, row in quotazioni.items()}
    roles = {pid: row.ruoli_codice for pid, row in quotazioni.items()}
    value = build_value({pid: row.fvm for pid, row in quotazioni.items()}, priced_ids=set(prices))
    legality = build_legality(load_compat())

    last = None
    for step in rolling_advisory(
        AstaState(total_budget=budget), pool, events,
        our_team_id=team, value=value, prices=prices, teams=teams, legality=legality, lam=lam,
    ):
        last = step
    if last is None:
        console.print("[dim]no sales in the replay[/dim]")
        return

    _, _, result, walkaways = last
    console.print(format_advisory(result, walkaways, names))
    opponents = track_opponents(events, our_team_id=team, roles_by_id=roles)
    console.print(format_opponents(opponents, names={}, total_budget=int(budget)))


COMMANDS = (asta_optimize, asta_legality, asta_live)


def register(app: typer.Typer) -> None:
    """Attach the offline asta commands to the root app."""
    for command in COMMANDS:
        app.command()(command)
