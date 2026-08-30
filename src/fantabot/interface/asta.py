"""The offline asta commands: `asta optimize` and `asta legality`. Read-only, no FantaLab.

The thin I/O shell: fetch the Mantra pool, values and prices from Postgres, hand them to the
pure engine (legality / value / optimizer / report), and print. Registered on the root app
by ``register(app)``, mirroring ``aste/cli.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

import typer

if TYPE_CHECKING:
    from fantabot.data_sources.models import SentimentRow

from fantabot.application.asta_planner import read_plan_inputs
from fantabot.domain.asta.legality import build_legality, fieldable_schemi, load_compat
from fantabot.domain.asta.live import normalize, resolve_ids
from fantabot.domain.asta.opponents import format_advisory, format_opponents, track_opponents
from fantabot.domain.asta.optimizer import InfeasibleRoster, optimize_roster
from fantabot.domain.asta.report import (
    build_pool,
    format_legality,
    format_roster,
    parse_ids,
    parse_replay_lines,
)
from fantabot.domain.asta.reservation import apply_event, reservations, rolling_advisory
from fantabot.domain.asta.sentiment import SentimentWeights
from fantabot.domain.asta.state import AstaState
from fantabot.interface.console import console
from fantabot.interface.options import SEASON, Season, Sentiment, SentimentRun, TiltK


class _SentimentSource(Protocol):
    """What the CLI asks of the sentiment feed. A Protocol so tests need no database."""

    def all_latest(
        self, *, data_run: date | None = ...
    ) -> dict[str, SentimentRow]: ...


def _today() -> date:
    """The one wall-clock read in the asta path. The golden harness patches exactly this.

    `sentiment.py` takes `as_of` as a parameter and never reads a clock, on purpose. This
    is the shell that supplies it — and it is a named function rather than three inline
    `date.today()` calls because the harness has to freeze it. `sentiment.py:153` decays
    confidence on a 7-day half-life, and every row shares one `data_run`, so one day of
    drift rescales every reading: the same inputs printed `obj 2273.1` today, `2209.1`
    tomorrow, `1936.5` in a week, with roster membership changing too. Three reads would
    be three things to freeze in lockstep, and `tests/test_asta_clock.py` keeps it at one.
    """
    return date.today()


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
            "Run `fantabot news fetch --write`, or pass --no-sentiment."
        )
    return rows


def asta_optimize(
    owned: str = typer.Option("", help="Player ids already owned, comma/space separated."),
    budget: float = typer.Option(500.0, help="Remaining credits to spend."),
    lam: float = typer.Option(0.0, "--lam", help="Risk aversion; higher diversifies across clubs."),
    fallbacks: int = typer.Option(3, help="How many next-best plans to show."),
    season: Season = SEASON,
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
) -> None:
    """Print the current optimal 30-man Mantra roster and next-best plans. Read-only."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.data_sources.news_sentiment import NewsSentimentSource

    with database_manager.get_session() as session:
        rows = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )
        world = read_plan_inputs(
            session, season=season, sentiment=rows, as_of=_today(), tilt_k=tilt_k
        )

    state = AstaState(owned=parse_ids(owned), total_budget=budget)

    try:
        result = optimize_roster(
            state,
            world.pool,
            value=world.value,
            prices=world.prices,
            teams=world.teams,
            legality=world.legality,
            lam=lam,
            n_fallbacks=fallbacks,
        )
    except InfeasibleRoster as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(format_roster(result.optimal, world.names, world.prices, sentiment=rows))
    for index, fallback in enumerate(result.fallbacks, start=1):
        console.print(
            f"[dim]fallback {index}: cost {fallback.total_cost:.0f} | obj {fallback.objective:.1f}[/dim]"
        )


def asta_legality(
    rosa: str = typer.Option(..., help="Player ids in the rosa, comma/space separated."),
    season: Season = SEASON,
) -> None:
    """Print which of the 11 Mantra schemi a rosa can field. Read-only."""
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.repositories.reference import ReferenceRepository

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
    season: Season = SEASON,
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
) -> None:
    """Render the rolling advisory off a captured replay (``--replay``) or a live room's sale
    ledger (``--league --db``). Read-only either way — the advisory advises, the human bids.

    The live path keys off the ``purchases/<fl>`` ledger (docs/fantalab/06 §10), not
    ``close_auction``, over the unauthenticated RTDB, and drives the exact same engine off
    ``AssignmentEvent`` as a replay does. It needs no token — only the shard.
    """
    from pathlib import Path

    from fantabot.adapters.persistence import database_manager

    if bool(league) == bool(replay):
        console.print("[red]Pass exactly one of --league or --replay.[/red]")
        raise typer.Exit(1)

    if league:
        if db < 0:
            console.print("[red]--league needs --db (the room's RTDB shard).[/red]")
            raise typer.Exit(1)
        from fantabot.adapters.http.fantalab import feed

        events = feed.ledger_events(db, league)
    else:
        rows = parse_replay_lines(Path(replay).read_text(encoding="utf-8").splitlines())
        events = normalize(row.get("state", row) for row in rows)

    # FantaLab identifies players by UUID; everything downstream is keyed by
    # fantacalcio id. Without this the first lot we own puts a UUID into
    # `AstaState.owned` and `optimize_roster` raises for an id absent from the pool.
    from fantabot.adapters.http.fantalab import listone

    events, unknown = resolve_ids(events, listone.fetch())
    if unknown:
        console.print(
            f"[yellow]{len(unknown)} sale(s) dropped — the listone does not know "
            f"those players, so they cannot be valued[/yellow]"
        )

    from fantabot.data_sources.news_sentiment import NewsSentimentSource

    # One reading per invocation, on both paths.
    #
    # The live path used to re-read per event, which bought nothing and cost a session and a
    # 548-row query each time (~11 ms). `feed.ledger_events` does a single HTTP GET and
    # returns a fully materialized list *before* the loop starts, so every event is already
    # known at t=0 — there is no "later" during which a fresher reading could arrive. The
    # replay path values on one reading for a different reason: a recording drifting as the
    # live table changes underneath it would mix two clocks.
    #
    # `rolling_advisory` still takes a factory rather than a model, and that is deliberate:
    # it is the seam a genuinely live `asta live` needs — which is why `PlanInputs` exposes
    # `value_of` rather than collapsing it. Re-reading only becomes meaningful once this
    # command polls the ledger each cycle the way `asta bid` already does, and at that point
    # the per-cycle read belongs there.
    with database_manager.get_session() as session:
        # `readings`, not `rows`: the replay branch above already binds `rows` to the
        # decoded JSONL lines, and shadowing it here would hand `read_plan_inputs` a
        # list of raw states. mypy caught that; nothing else would have.
        readings = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )
        world = read_plan_inputs(
            session, season=season, sentiment=readings, as_of=_today(), tilt_k=tilt_k
        )

    last = None
    for step in rolling_advisory(
        AstaState(total_budget=budget), world.pool, events,
        our_team_id=team, value_of=world.value_of, prices=world.prices, teams=world.teams,
        legality=world.legality, lam=lam,
    ):
        last = step
    if last is None:
        console.print("[dim]no sales in the replay[/dim]")
        return

    _, _, result, walkaways = last
    console.print(format_advisory(result, walkaways, world.names))
    opponents = track_opponents(events, our_team_id=team, roles_by_id=world.roles)
    console.print(format_opponents(opponents, names={}, total_budget=int(budget)))


def asta_bid(
    league: str = typer.Option(..., help="Fantaleague id of the live room."),
    db: int = typer.Option(..., help="The room's RTDB shard index (its `db` field; see docs/fantalab/06)."),
    team: str = typer.Option(..., help="Our fantateam id — the seat we bid from."),
    user: str = typer.Option(..., help="Our user id — rides on every bid."),
    budget: float = typer.Option(500.0, help="Our starting credits."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    season: Season = SEASON,
    poll: float = typer.Option(2.0, help="Seconds between polls."),
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
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

    from fantabot.adapters.http.fantalab import feed, room, rtdb
    from fantabot.adapters.persistence import database_manager
    from fantabot.data_sources.news_sentiment import NewsSentimentSource
    from fantabot.domain.asta.bid import Seat

    # The same value model asta optimize planned with, by construction now rather than by
    # maintenance: a walk-away is "what is he worth to us", and this is the one command
    # where that number becomes money. On plain fvm this loop would chase Yildiz to 62
    # credits with a metatarsal fracture reported by three sources.
    with database_manager.get_session() as session:
        readings = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )
        world = read_plan_inputs(
            session, season=season, sentiment=readings, as_of=_today(), tilt_k=tilt_k
        )

    # Fetched once for the run, not per poll: the mapping changes only when the
    # platform adds a player, and a live room does not want an HTTP round trip it
    # can avoid. See `fantalab/listone.py` for why this exists at all.
    from fantabot.adapters.http.fantalab import listone

    bridge = listone.fetch()
    if not bridge:
        console.print(
            "[red]no uuid -> fantacalcio_id bridge. Without it every lot we win is an "
            "unknown player and the planner refuses the roster.[/red]"
        )
        raise typer.Exit(code=1)

    seat = Seat(fantateam_id=team, user_id=user)

    def target_of(snapshot: Mapping[str, Any]) -> tuple[str, int] | None:
        player_id = snapshot.get("player_id")
        if not isinstance(player_id, str):
            return None
        state = AstaState(total_budget=budget)
        events, _unknown = resolve_ids(feed.ledger_events(db, league), bridge)
        for event in events:
            state = apply_event(state, event, our_team_id=team)
        _, walkaways = reservations(
            state,
            world.pool,
            value=world.value,
            prices=world.prices,
            teams=world.teams,
            legality=world.legality,
            lam=lam,
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


#: `(name, function)`. Explicit, because the group supplies the prefix: the command
#: is `asta optimize`, not `asta asta optimize`.
COMMANDS: tuple[tuple[str, Callable[..., None]], ...] = (
    ("optimize", asta_optimize),
    ("legality", asta_legality),
    ("live", asta_live),
    ("bid", asta_bid),
)


def register(asta: typer.Typer) -> None:
    """Attach the asta commands to their group."""
    for name, command in COMMANDS:
        asta.command(name)(command)
