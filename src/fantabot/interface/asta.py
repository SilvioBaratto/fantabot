"""The offline asta commands: `asta optimize` and `asta legality`. Read-only, no FantaLab.

The thin I/O shell: fetch the Mantra pool, values and prices from Postgres, hand them to the
pure engine (legality / value / optimizer / report), and print. Registered on the root app
by ``register(app)``, mirroring ``aste/cli.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

import typer

if TYPE_CHECKING:
    from fantabot.domain.shared.values import SentimentRow

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
from fantabot.domain.asta.reservation import (
    apply_event,
    price_floor,
    reservations,
    rolling_advisory,
)
from fantabot.domain.asta.sentiment import SentimentWeights
from fantabot.domain.asta.state import AstaState, RosterRules, drop_unvaluable
from fantabot.interface.console import console
from fantabot.interface.options import (
    SEASON,
    FloorAlpha,
    Season,
    Sentiment,
    SentimentRun,
    TiltK,
)


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


def _target_of(
    snapshot: Mapping[str, Any],
    bridge: Mapping[str, int],
    walkaways: Mapping[str, float],
) -> tuple[str, int] | None:
    """The lot on the block, priced — or ``None`` when it is not one of ours.

    **The third id-space gap, and the quietest.** ``resolve_ids`` re-keys the *ledger*, so
    ``AstaState.owned`` and every walk-away are fantacalcio ids; the lot arrives from
    somewhere else entirely — the raw ``auction/<fl>`` node — and is still a FantaLab UUID.
    Looking one up among the others misses on every lot, and ``run_bid_loop`` answers
    "not a target, hold" for the whole evening: no bid, no error, nothing to notice.

    Module-level rather than a closure so it can be tested at all: the solve it used to sit
    inside needs a database, a pool and a ledger, none of which this translation touches.

    The returned id is the **node's** uuid, not the fantacalcio id, because it goes back out
    in the bid payload and the platform refuses a raise naming a different lot
    (``docs/fantalab/06 §10.1``, test 5). Pricing happens on the translated id.
    """
    lot = snapshot.get("player_id")
    if not isinstance(lot, str):
        return None
    fantacalcio_id = bridge.get(lot)
    if fantacalcio_id is None:
        return None
    walk_away = walkaways.get(str(fantacalcio_id))
    # A walk-away of 0 is a target we will refuse on price, which is `decide_bid`'s call and
    # not this function's: collapsing it into `None` would make it indistinguishable from a
    # player the plan never named, and the heartbeat could no longer say which it saw.
    return (lot, int(walk_away)) if walk_away is not None else None


def bid_writer(
    *,
    auto_act: bool,
    arm: bool,
    send: Callable[[dict[str, Any]], Any],
    node: str = "auction",
) -> Callable[[dict[str, Any]], Any]:
    """``send`` if both locks are open, otherwise a write that goes nowhere.

    **Why two.** ``FANTABOT_AUTO_ACT`` is read inside ``place_raise`` at call time and comes
    from ``.env``, so flipping it arms every invocation at once — for the rest of that
    process and every process after it. The operator who edits ``.env`` in the morning is
    not necessarily the one running the command at 21:47.

    ``--arm`` is therefore *positive* and defaults off. The asymmetry decides the direction:
    forgetting an opt-in flag means watching, forgetting an opt-out flag means spending money.

    The disarmed writer returns a real ``BidOutcome`` rather than ``None``. The loop reads
    ``.sent`` through ``getattr``, so ``None`` would be accidentally right and would stop
    being right the moment anything else is read off it.
    """
    from fantabot.adapters.http.fantalab.rtdb import BidOutcome

    if auto_act and arm:
        return send

    def hold(payload: dict[str, Any]) -> BidOutcome:
        price = payload.get("price")
        # The same coercion `place_raise` applies: `True` is an `int` in Python and a
        # price of `True` is not a price.
        clean = price if isinstance(price, int) and not isinstance(price, bool) else 0
        return BidOutcome(price=clean, node=node, dry_run=True, sent=False, status=None)

    return hold


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
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource

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
    floor_alpha: FloorAlpha = 0.80,
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

    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource

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
        # The same floor the bidder uses. An advisory that shows a different number from the
        # command that spends the credits is the drift CLAUDE.md already records once.
        floor=price_floor(floor_alpha, world.prices) if floor_alpha else None,
    ):
        last = step
    if last is None:
        console.print("[dim]no sales in the replay[/dim]")
        return

    _, _, result, walkaways = last
    console.print(format_advisory(result, walkaways, world.names))
    opponents = track_opponents(events, our_team_id=team, roles_by_id=world.roles)
    console.print(format_opponents(opponents, names={}, total_budget=int(budget)))


def asta_calibrate(
    alpha: list[float] = typer.Option(
        [], "--alpha", help="Repeatable. Default sweeps 0.6 0.7 0.8 0.9 1.0."
    ),
    teams: int = typer.Option(8, help="Recorded league shape: number of teams."),
    credits: int = typer.Option(500, help="Recorded league shape: credits per team."),
    season: Season = SEASON,
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion, as the live commands use."),
) -> None:
    """Replay recorded aste at several walk-away floors. Read-only, no network.

    The evidence SPEC A6 gates arming on. `--floor-alpha` decides what the bot pays and is
    hand-set; this replays it against auctions that really happened and prints what each
    value would have spent. Pick the alpha whose spend lands near the budget with a rosa that
    can still field a schema, and paste the table into `tasks/todo.md`.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.adapters.persistence.repositories.aste import AsteRepository
    from fantabot.application.asta_calibrate import HEADER, Lot, RecordedAuction, sweep

    alphas = list(alpha) or [0.6, 0.7, 0.8, 0.9, 1.0]

    with database_manager.get_session() as session:
        rows = sentiment_rows(NewsSentimentSource(session), enabled=True, run="")
        world = read_plan_inputs(
            session, season=season, sentiment=rows, as_of=_today(), tilt_k=SentimentWeights().k
        )
        corpus = AsteRepository(session).recorded_auctions(
            num_teams=teams, num_credits=credits
        )

    auctions = [
        RecordedAuction(
            asta_id=asta_id,
            lots=tuple(Lot(player_id=pid, price=price, closed_at_ms=at) for pid, price, at in lots),
        )
        for asta_id, lots in corpus
    ]

    table = sweep(
        auctions, alphas,
        pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
        legality=world.legality, budget=float(credits), lam=lam,
    )
    if not table:
        console.print("[red]no alphas to sweep[/red]")
        raise typer.Exit(code=1)

    first = table[0]
    console.print(
        f"corpus: {first.auctions} of {first.auctions + first.dropped} auctions admitted "
        f"({first.dropped} dropped: fewer lots than the roster band needs)"
    )
    console.print(f"[dim]{HEADER}[/dim]")
    for row in table:
        console.print(row.line())


def asta_bid(
    league: str = typer.Option(..., help="Fantaleague id of the live room."),
    db: int = typer.Option(..., help="The room's RTDB shard index (its `db` field; see docs/fantalab/06)."),
    team: str = typer.Option(..., help="Our fantateam id — the seat we bid from."),
    user: str = typer.Option(..., help="Our user id — rides on every bid."),
    arm: bool = typer.Option(
        False, "--arm", help="Second, positive lock. Bidding is OFF without it."
    ),
    budget: float = typer.Option(500.0, help="Our starting credits."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    season: Season = SEASON,
    poll: float = typer.Option(2.0, help="Seconds between polls."),
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
    floor_alpha: FloorAlpha = 0.80,
) -> None:
    """Chase the advisory's targets in a live room, bidding each up to its walk-away.

    Read → decide → write, behind **two locks that must both be open**: ``FANTABOT_AUTO_ACT``
    in the environment *and* ``--arm`` on this invocation. Either one shut logs the intended bid
    and sends nothing. The env var is process-wide and comes from ``.env``, so on its own it arms
    every run for the rest of the day; ``--arm`` is what makes arming a thing the operator does
    now, deliberately, for this room. Participant only: it bids, it never settles a lot (that is the admin's
    close/confirm). The walk-aways re-plan each cycle off the live ``purchases/`` ledger, so they
    already account for what has been spent. Ctrl-C to stop.

    Fully unauthenticated: the shard (``--db``), seat (``--team``) and uid (``--user``) are given,
    and the live RTDB read + bid need no token (docs/fantalab/06 §10). The seat is claimed once,
    interactively; this command never touches the auth'd REST API.
    """
    import time

    # Fetched once for the run, not per poll: the mapping changes only when the
    # platform adds a player, and a live room does not want an HTTP round trip it
    # can avoid. See `fantalab/listone.py` for why this exists at all.
    #
    # Before the plan, not after: it is now an *input* to the plan, not only a translation
    # for the ledger. The pool has to be narrowed to players the room can actually call.
    from fantabot.adapters.http.fantalab import feed, listone, room, rtdb
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.config import settings
    from fantabot.domain.asta.bid import Seat, max_bid

    bridge = listone.fetch()
    if not bridge:
        console.print(
            "[red]no uuid -> fantacalcio_id bridge. Without it every lot we win is an "
            "unknown player and the planner refuses the roster.[/red]"
        )
        raise typer.Exit(code=1)

    # The same value model asta optimize planned with, by construction now rather than by
    # maintenance: a walk-away is "what is he worth to us", and this is the one command
    # where that number becomes money. On plain fvm this loop would chase Yildiz to 62
    # credits with a metatarsal fracture reported by three sources.
    #
    # `callable_ids` narrows the pool to what FantaLab's listone carries. 41 of 570 players
    # are absent from it and can never come up for auction, yet the optimizer planned around
    # them; in one simulated mid-auction state the top walk-away of all twelve targets was
    # Lukaku, who could not appear on the block at all.
    with database_manager.get_session() as session:
        readings = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )
        world = read_plan_inputs(
            session,
            season=season,
            sentiment=readings,
            as_of=_today(),
            tilt_k=tilt_k,
            callable_ids={str(fid) for fid in bridge.values()},
        )

    seat = Seat(fantateam_id=team, user_id=user)

    # Said before the first poll, not after: the operator has to be able to tell an armed run
    # from a rehearsal at a glance, and the heartbeat that follows looks identical either way.
    if settings.fantabot_auto_act and arm:
        console.print("[bold red]● ARMED — bids are real credits[/bold red]")
    else:
        why = "--arm not given" if settings.fantabot_auto_act else "FANTABOT_AUTO_ACT is false"
        console.print(f"[dim]DRY RUN — nothing will be sent ({why})[/dim]")

    # Said once per uuid rather than once per poll: at a 2 s cycle the same handful of
    # strangers would otherwise scroll the heartbeat off the screen within a minute, and
    # the heartbeat is the only thing the operator is reading.
    reported: set[str] = set()

    def _report_once(uuids: Iterable[str], why: str) -> None:
        fresh = sorted({u for u in uuids} - reported)
        if not fresh:
            return
        reported.update(fresh)
        console.print(f"[yellow]{len(fresh)} {why}: {', '.join(fresh)}[/yellow]")

    # What the last cycle's fold of the ledger said we still hold. The loop's budget guard
    # reads it, so it has to be the number the plan was just built against rather than the
    # credits we started the evening with: after one lot won, those two stop agreeing.
    remaining = [int(budget)]
    # The MAX, recomputed from the same fold: credits left, against the slots the band still
    # owes. It follows `drop_unvaluable`'s shrunk band down, or it would reserve credits for
    # slots an unvaluable player we already hold has quietly filled.
    cap = [max_bid(int(budget), RosterRules().size)]

    def target_of(snapshot: Mapping[str, Any]) -> tuple[str, int] | None:
        state = AstaState(total_budget=budget)
        events, unknown = resolve_ids(feed.ledger_events(db, league), bridge)
        # A sale the listone cannot name is a sale we do not subtract: the buyer's budget
        # and the player's availability both stay wrong for the rest of the evening. Silence
        # here reads as "the ledger was clean", which is the failure `resolve_ids` counts for.
        _report_once(unknown, "sale(s) dropped, uuid not in the listone")
        for event in events:
            state = apply_event(state, event, our_team_id=team)
        # A player we won who is in FantaLab's listone but not in our `quotazioni` would make
        # `optimize_roster` refuse the state — and refuse it again every cycle, because the
        # ledger is re-read each time and a purchase is never withdrawn. Setting him aside
        # here is what turns "stopped for the evening" into "held for one lot".
        state, rules, unvaluable = drop_unvaluable(state, world.pool, RosterRules())
        _report_once(unvaluable, "owned player(s) we cannot value; roster band shrunk")
        remaining[0] = int(state.remaining_budget)
        cap[0] = max_bid(remaining[0], rules.size - len(state.owned))
        _, walkaways = reservations(
            state,
            world.pool,
            value=world.value,
            prices=world.prices,
            teams=world.teams,
            legality=world.legality,
            rules=rules,
            lam=lam,
            n_targets=None,
            floor=price_floor(floor_alpha, world.prices),
        )
        lot = snapshot.get("player_id")
        if isinstance(lot, str) and lot not in bridge:
            # Distinguishable from "we chose not to chase him": the loop's own heartbeat
            # says "not a target, hold" for both, and only this line separates them.
            _report_once([lot], "lot(s) we cannot value, uuid not in the listone")
        return _target_of(snapshot, bridge, walkaways)

    report = room.run_bid_loop(
        seat=seat,
        fantaleague_id=league,
        remaining_budget=lambda: remaining[0],
        max_cap=lambda: cap[0],
        target_of=target_of,
        read=lambda: rtdb.read_snapshot(db, f"auction/{league}"),
        write=bid_writer(
            auto_act=settings.fantabot_auto_act,
            arm=arm,
            send=lambda payload: rtdb.place_raise(db, league, payload),
        ),
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
    ("calibrate", asta_calibrate),
)


def register(asta: typer.Typer) -> None:
    """Attach the asta commands to their group."""
    for name, command in COMMANDS:
        asta.command(name)(command)
