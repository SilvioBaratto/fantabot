"""The offline asta commands: `asta-optimize` and `asta-legality`. Read-only, no FantaLab.

The thin I/O shell: fetch the Mantra pool, values and prices from Postgres, hand them to the
pure engine (legality / value / optimizer / report), and print. Registered on the root app
by ``register(app)``, mirroring ``aste/cli.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

import typer
from rich.console import Console

if TYPE_CHECKING:
    from fantabot.data_sources.models import SentimentRow

from .legality import build_legality, fieldable_schemi, load_compat
from .live import normalize
from .opponents import format_advisory, format_opponents, track_opponents
from .optimizer import InfeasibleRoster, optimize_roster
from .prices import expected_prices
from .report import (
    build_pool,
    build_value,
    format_legality,
    format_roster,
    parse_ids,
    parse_replay_lines,
)
from .reservation import apply_event, reservations, rolling_advisory
from .sentiment import SentimentWeights
from .state import AstaState
from .value import ValueModel

console = Console()

_SEASON = "2026/27"


class _SentimentSource(Protocol):
    """What the CLI asks of the sentiment feed. A Protocol so tests need no database."""

    def all_latest(
        self, *, data_run: date | None = ...
    ) -> dict[str, SentimentRow]: ...


def parse_run_date(run: str) -> date | None:
    """``--sentiment-run`` as a date; empty means "each player's newest". Pure."""
    if not run:
        return None
    try:
        return date.fromisoformat(run)
    except ValueError as exc:
        raise typer.BadParameter(
            f"--sentiment-run must be YYYY-MM-DD, got {run!r}"
        ) from exc


def sentiment_rows(
    source: _SentimentSource, *, enabled: bool, run: str
) -> dict[str, SentimentRow] | None:
    """Fetch the readings, or ``None`` when the operator asked for the ablation.

    An empty result is refused rather than passed through. Valuing on "no rows" is
    numerically identical to ``--no-sentiment`` but means something entirely different, and
    a run that silently plans on plain ``fvm`` because a date was mistyped is exactly the
    failure this check exists to prevent.
    """
    if not enabled:
        return None

    pinned = parse_run_date(run)
    rows = source.all_latest(data_run=pinned)
    if not rows:
        where = f"for data_run {run}" if run else "in the database"
        raise typer.BadParameter(
            f"sentiment is on but there are no rows {where}. "
            "Run `fantabot news-fetch --write`, or pass --no-sentiment."
        )
    return rows


def asta_optimize(
    owned: str = typer.Option("", help="Player ids already owned, comma/space separated."),
    budget: float = typer.Option(500.0, help="Remaining credits to spend."),
    lam: float = typer.Option(0.0, "--lam", help="Risk aversion; higher diversifies across clubs."),
    fallbacks: int = typer.Option(3, help="How many next-best plans to show."),
    season: str = typer.Option(_SEASON, help="Which stagione's Mantra listone."),
    sentiment: bool = typer.Option(
        True,
        "--sentiment/--no-sentiment",
        help="Adjust values by the news feed. --no-sentiment is the fvm-only ablation.",
    ),
    sentiment_run: str = typer.Option(
        "", help="Pin sentiment to one data_run (YYYY-MM-DD); default is each player's newest."
    ),
    tilt_k: float = typer.Option(
        SentimentWeights().k,
        "--tilt-k",
        help="Strength of the quality tilt. 0 uses the playing-time gate alone.",
    ),
) -> None:
    """Print the current optimal 30-man Mantra roster and next-best plans. Read-only."""
    from fantabot.data_sources.news_sentiment import NewsSentimentSource
    from fantabot.db import database_manager
    from fantabot.db.repositories.reference import ReferenceRepository

    with database_manager.get_session() as session:
        quotazioni = ReferenceRepository(session).quotazioni(season, "mantra")
        prices = expected_prices(session)
        rows = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )

    pool = build_pool({pid: row.ruoli_codice for pid, row in quotazioni.items()})
    teams = {pid: row.squadra for pid, row in quotazioni.items()}
    names = {pid: row.nome for pid, row in quotazioni.items()}
    value = build_value(
        {pid: row.fvm for pid, row in quotazioni.items()},
        priced_ids=set(prices),
        sentiment=rows,
        as_of=date.today() if rows else None,
        weights=SentimentWeights(k=tilt_k),
    )
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

    console.print(format_roster(result.optimal, names, prices, sentiment=rows))
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
    replay: str = typer.Option("", help="Path to a JSONL of raw room states (a captured room)."),
    league: str = typer.Option(
        "", help="Fantaleague id of a live room — reads its sale ledger (alternative to --replay)."
    ),
    db: int = typer.Option(-1, help="The room's RTDB shard (its `db` field) — required with --league."),
    team: str = typer.Option(..., help="Our team id in the room."),
    budget: float = typer.Option(500.0, help="Our starting credits."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    season: str = typer.Option(_SEASON, help="Which stagione's Mantra listone."),
    sentiment: bool = typer.Option(
        True,
        "--sentiment/--no-sentiment",
        help="Adjust values by the news feed. On by default, matching asta-optimize.",
    ),
    sentiment_run: str = typer.Option(
        "", help="Pin sentiment to one data_run (YYYY-MM-DD); default is each player's newest."
    ),
    tilt_k: float = typer.Option(
        SentimentWeights().k, "--tilt-k", help="Strength of the quality tilt. 0 = gate only."
    ),
) -> None:
    """Render the rolling advisory off a captured replay (``--replay``) or a live room's sale
    ledger (``--league --db``). Read-only either way — the advisory advises, the human bids.

    The live path keys off the ``purchases/<fl>`` ledger (docs/fantalab/06 §10), not
    ``close_auction``, over the unauthenticated RTDB, and drives the exact same engine off
    ``AssignmentEvent`` as a replay does. It needs no token — only the shard.
    """
    from pathlib import Path

    from fantabot.db import database_manager
    from fantabot.db.repositories.reference import ReferenceRepository

    if bool(league) == bool(replay):
        console.print("[red]Pass exactly one of --league or --replay.[/red]")
        raise typer.Exit(1)

    if league:
        if db < 0:
            console.print("[red]--league needs --db (the room's RTDB shard).[/red]")
            raise typer.Exit(1)
        from fantabot.fantalab import feed

        events = feed.ledger_events(db, league)
    else:
        rows = parse_replay_lines(Path(replay).read_text(encoding="utf-8").splitlines())
        events = normalize(row.get("state", row) for row in rows)

    from fantabot.data_sources.news_sentiment import NewsSentimentSource

    with database_manager.get_session() as session:
        quotazioni = ReferenceRepository(session).quotazioni(season, "mantra")
        prices = expected_prices(session)

    pool = build_pool({pid: row.ruoli_codice for pid, row in quotazioni.items()})
    teams = {pid: row.squadra for pid, row in quotazioni.items()}
    names = {pid: row.nome for pid, row in quotazioni.items()}
    roles = {pid: row.ruoli_codice for pid, row in quotazioni.items()}
    fvm = {pid: row.fvm for pid, row in quotazioni.items()}
    weights = SentimentWeights(k=tilt_k)
    legality = build_legality(load_compat())

    def read_value() -> ValueModel:
        with database_manager.get_session() as fresh:
            rows = sentiment_rows(
                NewsSentimentSource(fresh), enabled=sentiment, run=sentiment_run
            )
        return build_value(
            fvm,
            priced_ids=set(prices),
            sentiment=rows,
            as_of=date.today() if rows else None,
            weights=weights,
        )

    # A replay is a recording: it values on one reading, because letting a captured room
    # drift as the live table changes underneath it would mix two clocks. A live room
    # re-reads, so a player ruled out mid-asta stops being a target on the next sale rather
    # than on the next restart. Each read opens its own short session — sales are minutes
    # apart, and an hours-long idle transaction is the worse trade.
    value_of = read_value if league else (lambda snapshot=read_value(): snapshot)

    last = None
    for step in rolling_advisory(
        AstaState(total_budget=budget), pool, events,
        our_team_id=team, value_of=value_of, prices=prices, teams=teams, legality=legality,
        lam=lam,
    ):
        last = step
    if last is None:
        console.print("[dim]no sales in the replay[/dim]")
        return

    _, _, result, walkaways = last
    console.print(format_advisory(result, walkaways, names))
    opponents = track_opponents(events, our_team_id=team, roles_by_id=roles)
    console.print(format_opponents(opponents, names={}, total_budget=int(budget)))


def asta_bid(
    league: str = typer.Option(..., help="Fantaleague id of the live room."),
    db: int = typer.Option(..., help="The room's RTDB shard index (its `db` field; see docs/fantalab/06)."),
    team: str = typer.Option(..., help="Our fantateam id — the seat we bid from."),
    user: str = typer.Option(..., help="Our user id — rides on every bid."),
    budget: float = typer.Option(500.0, help="Our starting credits."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    season: str = typer.Option(_SEASON, help="Which stagione's Mantra listone."),
    poll: float = typer.Option(2.0, help="Seconds between polls."),
) -> None:
    """Chase the advisory's targets in a live room, bidding each up to its walk-away.

    Read → decide → write, **gated by FANTABOT_AUTO_ACT** — off (the default) logs the intended
    bid and sends nothing. Participant only: it bids, it never settles a lot (that is the admin's
    close/confirm). The walk-aways re-plan each cycle off the live ``purchases/`` ledger, so they
    already account for what has been spent. Ctrl-C to stop.

    Fully unauthenticated: the shard (``--db``), seat (``--team``) and uid (``--user``) are given,
    and the live RTDB read + bid need no token (docs/fantalab/06 §10). The seat is claimed once,
    interactively; this command never touches the auth'd REST API.
    """
    import time

    from fantabot.asta_engine.bid import Seat
    from fantabot.db import database_manager
    from fantabot.db.repositories.reference import ReferenceRepository
    from fantabot.fantalab import feed, room, rtdb

    with database_manager.get_session() as session:
        quotazioni = ReferenceRepository(session).quotazioni(season, "mantra")
        prices = expected_prices(session)
    pool = build_pool({pid: row.ruoli_codice for pid, row in quotazioni.items()})
    teams = {pid: row.squadra for pid, row in quotazioni.items()}
    value = build_value({pid: row.fvm for pid, row in quotazioni.items()}, priced_ids=set(prices))
    legality = build_legality(load_compat())
    seat = Seat(fantateam_id=team, user_id=user)

    def target_of(snapshot: Mapping[str, Any]) -> tuple[str, int] | None:
        player_id = snapshot.get("player_id")
        if not isinstance(player_id, str):
            return None
        state = AstaState(total_budget=budget)
        for event in feed.ledger_events(db, league):
            state = apply_event(state, event, our_team_id=team)
        _, walkaways = reservations(
            state, pool, value=value, prices=prices, teams=teams, legality=legality, lam=lam
        )
        walk_away = walkaways.get(player_id)
        return (player_id, int(walk_away)) if walk_away is not None else None

    report = room.run_bid_loop(
        seat=seat,
        fantaleague_id=league,
        remaining_budget=int(budget),
        target_of=target_of,
        read=lambda: rtdb.read_snapshot(db, f"auction/{league}"),
        write=lambda payload: rtdb.place_raise(db, league, payload),
        now=lambda: int(time.time() * 1000),
        sleep=time.sleep,
        keep_going=lambda _cycle: True,
        heartbeat=console.print,
        poll_seconds=poll,
    )
    console.print(
        f"[dim]stopped: {report.cycles} cycles, {report.bids_sent} bids, "
        f"refused {report.refused}[/dim]"
    )


COMMANDS = (asta_optimize, asta_legality, asta_live, asta_bid)


def register(app: typer.Typer) -> None:
    """Attach the offline asta commands to the root app."""
    for command in COMMANDS:
        app.command()(command)
