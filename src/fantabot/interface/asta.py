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
    from rich.console import RenderableType

    from fantabot.domain.shared.values import SentimentRow

from pathlib import Path

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
from fantabot.domain.asta.reservation import rolling_advisory
from fantabot.domain.asta.sentiment import SentimentWeights
from fantabot.domain.asta.state import AstaState, RosterRules
from fantabot.interface.console import console
from fantabot.interface.options import (
    SEASON,
    BargainBeta,
    BargainShare,
    CeilingAlpha,
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


def _callable_ids(
    warn: Callable[[str], None],
    *,
    _fetch: Callable[[], Mapping[str, int]] | None = None,
) -> set[str] | None:
    """The fantacalcio ids FantaLab's listone can actually call, or ``None`` if unknown.

    ``None`` means "do not filter". It is deliberately not ``set()``: `read_plan_inputs` reads
    an empty collection as a real, total exclusion — right for the bidder, where an unresolved
    bridge means every lot would be unknown — and it would empty the pool here. A planner that
    refuses to plan because a CDN was unreachable is worse than one that plans over a slightly
    wider pool and says so, because this is the command an operator runs the night before, and
    its output is the paper fallback for the evening.

    Measured 2026-09-01: 41 of 570 pool players are absent from the listone. Lukaku (2531) is
    one of them — priced at fvm 41 in `quotazioni`, so the optimiser sees him, and absent from
    the listone, so the room can never call him. He took a slot in the printed 30-man plan,
    which therefore had 29 fillable places and one that could not be filled.

    ``_fetch`` is the injection seam, so the suite covers both degradations without a socket.
    """
    from fantabot.adapters.http.fantalab import listone

    fetch = _fetch or listone.fetch
    try:
        bridge = fetch()
    except Exception as exc:  # any transport failure degrades the same way
        warn(f"listone unreachable ({type(exc).__name__}); planning over the whole pool")
        return None
    if not bridge:
        warn("listone empty; planning over the whole pool")
        return None
    return {str(fid) for fid in bridge.values()}


def _report_stopped(report: Any) -> None:
    """The exit summary both live commands print. One place, because they must not diverge.

    `errors` gets its own red line. `400 cycles, 0 bids` reads as an evening in which nothing
    we wanted came up, and that is indistinguishable — in that one line — from a link that was
    down the whole time.
    """
    console.print(
        f"[dim]stopped: {report.cycles} cycles, {report.bids_sent} bids, "
        f"refused {report.refused}[/dim]"
    )
    if report.errors:
        total = sum(report.errors.values())
        console.print(f"[red]{total} cycle(s) failed on the link: {report.errors}[/red]")


def asta_optimize(
    owned: str = typer.Option("", help="Player ids already owned, comma/space separated."),
    budget: float = typer.Option(500.0, help="Remaining credits to spend."),
    lam: float = typer.Option(0.0, "--lam", help="Risk aversion; higher diversifies across clubs."),
    fallbacks: int = typer.Option(3, help="How many next-best plans to show."),
    callable_only: bool = typer.Option(
        True,
        "--callable/--no-callable",
        help="Plan only over players FantaLab's listone can call. Cached; falls back open.",
    ),
    season: Season = SEASON,
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
) -> None:
    """Print the current optimal 30-man Mantra roster and next-best plans. Read-only.

    The pool is narrowed to players FantaLab's listone can call, exactly as `asta bid` narrows
    it. It is the same plan or it is not a plan: this is what the operator reads the night
    before and bids from by hand, and a player who can never come up for auction is a slot the
    evening cannot fill. `--no-callable` restores the old, wider plan.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource

    ids = (
        _callable_ids(lambda note: console.print(f"[yellow]{note}[/yellow]"))
        if callable_only
        else None
    )

    with database_manager.get_session() as session:
        rows = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )
        world = read_plan_inputs(
            session, season=season, sentiment=rows, as_of=_today(), tilt_k=tilt_k,
            callable_ids=ids,
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

    bridge = listone.fetch()
    events, unknown = resolve_ids(events, bridge)
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
            session, season=season, sentiment=readings, as_of=_today(), tilt_k=tilt_k,
            # The bridge is already in hand for `resolve_ids`; the same narrowing the bidder
            # applies. This is the advisory an operator bids by hand from when the room view
            # is gone, so it must not head its list with a player who cannot be called.
            callable_ids={str(fid) for fid in bridge.values()} if bridge else None,
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


def asta_room(
    url: str = typer.Argument(..., help="The FantaLab room link, or its fantaleague id."),
    arm: bool = typer.Option(
        False, "--arm", help="Second, positive lock. Bidding is OFF without it."
    ),
    resolve_only: bool = typer.Option(
        False, "--resolve-only", help="Print the resolved room and exit. Touches no RTDB."
    ),
    budget: float = typer.Option(
        0.0, help="Our starting credits. 0 reads the room's own num_credits."
    ),
    limit: int = typer.Option(40, help="Listone rows rendered."),
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion; higher diversifies across clubs."),
    poll: float = typer.Option(2.0, help="Seconds between polls."),
    season: Season = SEASON,
    sentiment: Sentiment = True,
    sentiment_run: SentimentRun = "",
    tilt_k: TiltK = SentimentWeights().k,
    ceiling_alpha: CeilingAlpha = 1.00,
    bargain_beta: BargainBeta = 0.00,
    bargain_share: BargainShare = 0.10,
    copilot: bool = typer.Option(True, "--copilot/--no-copilot", help="The LLM pane."),
    brief_top: int = typer.Option(40, help="How many of the plan's targets to pre-brief."),
) -> None:
    """The live room: the listone, the lot, the model's bidding and the rosa, on one screen.

    `fantabot asta room "https://app.fantalab.it/asta?asta=<uuid>"`.

    Not `fantabot <url>`: a root callback with a positional argument makes Click consume the
    first token as that argument, so `fantabot asta bid` would exit 2 with `No such command
    'bid'` and all 22 commands would break. A shell alias recovers the ergonomics:
    `fanta() { fantabot asta room "$1"; }`.

    **Two locks before a credit is spent** — `FANTABOT_AUTO_ACT` *and* `--arm`, both opt-in.
    Ctrl-C once disarms and keeps watching; twice exits. Mid-auction "stop bidding, keep
    showing me the room" is far more often what is wanted than "quit".

    ⚠ The authenticated fetch behind `--resolve-only` had no caller in `src/` before this
    phase. If it fails, `harvest scan --seed` still yields the shard and the room's settings.
    """
    import contextlib
    import signal
    import time

    from fantabot.adapters.files.room_journal import RoomJournal
    from fantabot.adapters.http.fantalab import feed, listone, rest, room, rtdb
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.adapters.tokens.fantalab_store import FantalabStore
    from fantabot.application.asta_copilot import CopilotWorker, briefs_for
    from fantabot.application.asta_room import RoomFrame, RoomRefused, RoomTracker, resolve_room
    from fantabot.config import settings
    from fantabot.domain.asta.bid import Seat, max_bid
    from fantabot.domain.asta.live import InvitationLink, parse_room_url
    from fantabot.domain.asta.report import listone_rows
    from fantabot.domain.tokens.crypto import TokenCipher
    from fantabot.interface.room_view import error_overlay, render

    try:
        fantaleague_id = parse_room_url(url)
    except InvitationLink as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    cipher = TokenCipher(settings.fantabot_encryption_key)
    with database_manager.get_session() as session:
        stored = FantalabStore(session, cipher).load()
    if stored is None or not stored.id_token or not stored.user_id:
        console.print("[red]No FantaLab session stored. Run: fantabot auth fantalab-login[/red]")
        raise typer.Exit(code=2)

    # The token is bound here and goes no further: `resolve_room` takes a callable, so the
    # application layer never holds a credential and cannot render one by accident.
    try:
        resolved = resolve_room(
            fantaleague_id,
            user_id=stored.user_id,
            fetch=lambda fl: rest.fetch_league(fl, token=stored.id_token),
        )
    except RoomRefused as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[bold]{resolved.fantaleague_id}[/bold] · shard {resolved.db} · "
        f"{resolved.asta_mode}/{resolved.raise_mode} · "
        f"{resolved.num_teams} teams x {resolved.num_credits} credits · "
        f"seat {resolved.seat.team_name or resolved.seat.fantateam_id}"
    )
    if resolve_only:
        return

    bridge = listone.fetch()
    if not bridge:
        console.print("[red]no uuid -> fantacalcio_id bridge; every lot would be unknown[/red]")
        raise typer.Exit(code=1)

    credits = budget or resolved.budget
    with database_manager.get_session() as session:
        readings = sentiment_rows(
            NewsSentimentSource(session), enabled=sentiment, run=sentiment_run
        )
        world = read_plan_inputs(
            session, season=season, sentiment=readings, as_of=_today(), tilt_k=tilt_k,
            callable_ids={str(fid) for fid in bridge.values()},
            num_teams=resolved.num_teams or 8,
            num_credits=int(resolved.num_credits or 500),
        )

    # Arming is a positive act twice over: the env var alone arms every run for the rest of
    # the day, and the operator who edits `.env` in the morning is not the one at the keyboard
    # at 21:47. `armed` is a list so the SIGINT handler can disarm it without a global.
    armed = [bool(settings.fantabot_auto_act and arm)]
    if armed[0] and not typer.confirm(
        f"Bid REAL CREDITS in {resolved.fantaleague_id} as "
        f"{resolved.seat.team_name or resolved.seat.fantateam_id}, budget {credits:.0f}?"
    ):
        raise typer.Abort

    router = room.LotRouter(
        read=lambda node: rtdb.read_snapshot(resolved.db, f"{node}/{resolved.fantaleague_id}"),
        write=lambda payload, node: rtdb.place_raise(
            resolved.db, resolved.fantaleague_id, payload, node=node
        ),
    )
    journal = RoomJournal(Path(settings.fantabot_data_dir) / "room_journal.jsonl")
    tracker = RoomTracker(
        seat=Seat(
            fantateam_id=resolved.seat.fantateam_id, user_id=stored.user_id
        ),
        bridge=bridge,
        pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
        legality=world.legality, names=world.names,
        rules=RosterRules(),
        budget=credits,
        lam=lam,
        ceiling_alpha=ceiling_alpha,
        bargain_beta=bargain_beta,
        bargain_share=bargain_share,
        admin_user_id=resolved.admin_id,
        seat_by_user=resolved.seat_by_user,
        ledger=lambda: feed.ledger_events(resolved.db, resolved.fantaleague_id),
        journal=journal.write,
        counter_time=resolved.counter_time,
        counter_time_first=resolved.counter_time_first,
    )

    # First Ctrl-C disarms and keeps drawing; second exits. Mid-auction the operator far more
    # often wants "stop bidding, keep showing me the room" than "quit" — the pattern is
    # `news fetch`'s, for the same reason.
    previous_sigint = signal.getsignal(signal.SIGINT)

    def _disarm(_signum: int, _frame: Any) -> None:
        if not armed[0]:
            signal.signal(signal.SIGINT, previous_sigint)
            raise KeyboardInterrupt
        armed[0] = False
        console.print("[yellow]disarmed — still watching. Ctrl-C again to exit.[/yellow]")

    # Not the main thread means no signal handler, which costs the graceful disarm and
    # nothing else. Refusing to run the room over that would be the worse trade.
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGINT, _disarm)

    # One slot, not a log: only `latest[-1]` is ever read, and a frame per poll for three
    # hours is thousands of walk-away dicts held by a process that must not die mid-auction.
    latest: list[RoomFrame] = []
    #: The last painted screen, so `on_error` can redraw it under a banner. One slot, for the
    #: same reason `latest` is one slot.
    screen: list[RenderableType] = []

    # Out of band, on a daemon thread. `counter_time` is 7-10 s and a query takes seconds, so
    # asking about the lot on the block would answer about a lot that has already closed —
    # the targets are briefed ahead and looked up when they come up.
    worker = CopilotWorker() if copilot else None
    briefed: set[str] = set()
    if worker is not None:
        worker.start()

    def target_of(snapshot: Mapping[str, Any]) -> tuple[str, int] | None:
        frame = tracker.cycle(
            snapshot, now_ms=int(time.time() * 1000), node=router.node
        )
        latest[:] = [frame]

        advice = None
        if worker is not None:
            fresh = [pid for pid in list(frame.walkaways)[:brief_top] if pid not in briefed]
            if fresh:
                briefed.update(fresh)
                worker.brief(
                    briefs_for(
                        fresh,
                        names=dict(world.names), teams=dict(world.teams),
                        roles={k: list(v) for k, v in world.roles.items()},
                        walkaways=dict(frame.walkaways), prices=dict(world.prices),
                        credits_left=frame.credits_left,
                        slots_left=RosterRules().size - len(frame.owned),
                        schemi_open=frame.schemi_open,
                        recent=frame.recent,
                    )
                )
            lot_fid = bridge.get(frame.lot_id) if frame.lot_id else None
            advice = worker.advice_for(str(lot_fid)) if lot_fid else None

        rows = listone_rows(
            world.pool, AstaState(owned=frame.owned, total_budget=credits),
            names=world.names, teams=world.teams, prices=world.prices, value=world.value,
            walkaways=frame.walkaways, limit=limit,
        )
        view = render(
            frame, rows, room=resolved, armed=armed[0], advice=advice,
            copilot_offline=worker is not None and worker.offline,
        )
        screen[:] = [view]
        live.update(view)
        if frame.target is None or frame.walk_away is None:
            return None
        return (frame.target, frame.walk_away)

    def on_error(exc: Exception, consecutive: int) -> None:
        """A failed poll, on the screen the operator is actually looking at."""
        live.update(
            error_overlay(
                screen[0] if screen else None,
                f"{type(exc).__name__}: {exc}",
                consecutive=consecutive,
            )
        )

    from rich.live import Live

    with Live(console=console, screen=True, refresh_per_second=4) as live:
        report = room.run_bid_loop(
            seat=Seat(fantateam_id=resolved.seat.fantateam_id, user_id=stored.user_id),
            fantaleague_id=resolved.fantaleague_id,
            remaining_budget=lambda: latest[-1].credits_left if latest else int(credits),
            max_cap=lambda: latest[-1].max_cap if latest else max_bid(int(credits), RosterRules().size),
            target_of=target_of,
            read=lambda: router.read_lot()[0],
            # Bound per call, not once: `armed[0]` is what the first Ctrl-C clears, and a
            # writer captured at loop start would keep bidding after the operator disarmed.
            write=lambda payload: bid_writer(
                auto_act=settings.fantabot_auto_act,
                arm=armed[0],
                send=router.write_raise,
                node=router.node,
            )(payload),
            now=lambda: int(time.time() * 1000),
            sleep=time.sleep,
            keep_going=lambda _cycle: True,
            # The room's heartbeat has nowhere to go — the screen is the frame. Errors do not
            # go through it either: they went through a filter on the line's *text*, which
            # missed `ReadTimeout`, `ConnectTimeout` and `PoolTimeout` — on a flaky link the
            # three most likely of all — and printed the rest into an alternate buffer the
            # next refresh overwrote. They are painted into the Live now, by `on_error`.
            heartbeat=lambda _line: None,
            on_error=on_error,
            poll_seconds=poll,
        )

    if worker is not None:
        worker.stop()
    journal.close()
    signal.signal(signal.SIGINT, previous_sigint)
    _report_stopped(report)


def asta_calibrate(
    alpha: list[float] = typer.Option(
        [], "--alpha", help="Repeatable. Default sweeps 0.85 0.90 0.95 1.00 1.05 1.10 1.15."
    ),
    teams: int = typer.Option(8, help="Recorded league shape: number of teams."),
    credits: int = typer.Option(500, help="Recorded league shape: credits per team."),
    season: Season = SEASON,
    lam: float = typer.Option(0.3, "--lam", help="Risk aversion, as the live commands use."),
) -> None:
    """Replay recorded aste at several ceiling premiums. Read-only, no network.

    The evidence SPEC A6 gates arming on. `--ceiling-alpha` is the premium applied on top of
    `lot_ceiling`'s own re-solved number, and it is hand-set; this replays it against auctions
    that really happened and prints what each value would have spent. Pick the alpha whose
    spend lands near the budget with a rosa that can still field a schema, and paste the table
    into `tasks/todo.md`.
    """
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.adapters.persistence.repositories.aste import AsteRepository
    from fantabot.application.asta_calibrate import HEADER, Lot, RecordedAuction, sweep

    alphas = list(alpha) or [0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15]

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
    ceiling_alpha: CeilingAlpha = 1.00,
    bargain_beta: BargainBeta = 0.00,
    bargain_share: BargainShare = 0.10,
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

    ⚠ **A trade-off of that, not fixed here**: a lot the admin lets a stood raise fall through
    on (Task 2.2's defect — the ledger records it identically to a routine admin skip) is only
    reattributed when the room's seats and admin uid are known, and those live on
    ``RoomConfig``, reached only through the authenticated fetch this command deliberately
    skips. ``asta room`` (which does authenticate) catches it; this command does not.
    """
    import time

    from fantabot.adapters.files.room_journal import RoomJournal

    # Fetched once for the run, not per poll: the mapping changes only when the
    # platform adds a player, and a live room does not want an HTTP round trip it
    # can avoid. See `fantalab/listone.py` for why this exists at all.
    #
    # Before the plan, not after: it is now an *input* to the plan, not only a translation
    # for the ledger. The pool has to be narrowed to players the room can actually call.
    from fantabot.adapters.http.fantalab import feed, listone, room, rtdb
    from fantabot.adapters.persistence import database_manager
    from fantabot.adapters.persistence.news_sentiment import NewsSentimentSource
    from fantabot.application.asta_room import RoomFrame, RoomTracker
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

    journal = RoomJournal(Path(settings.fantabot_data_dir) / "room_journal.jsonl")
    tracker = RoomTracker(
        seat=seat,
        bridge=bridge,
        pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
        legality=world.legality, names=world.names,
        rules=RosterRules(),
        budget=budget,
        lam=lam,
        ceiling_alpha=ceiling_alpha,
        bargain_beta=bargain_beta,
        bargain_share=bargain_share,
        # No admin_user_id / seat_by_user: both live on `RoomConfig`, reached only through the
        # authenticated `rest.fetch_league` this command deliberately never calls (see its own
        # docstring). A passed lot the admin actually let stand is invisible here the same way
        # it always was — `RoomTracker` degrades to that, not to a crash, without them.
        ledger=lambda: feed.ledger_events(db, league),
        journal=journal.write,
        counter_time=None, counter_time_first=None,
    )

    # One code path, not two. `asta bid` used to carry its own copy of this fold — and
    # `CLAUDE.md` records where that leads: three commands each grew their own value model and
    # the one that spent credits fell behind the one that advised.
    latest: list[RoomFrame] = []
    reported: set[str] = set()

    # Both nodes, not just `auction/`. Under ASSEGNA random the lot lands on `assign/<fl>`
    # and a bidder watching only the first sees an empty room all evening (docs/fantalab/06
    # §10.6). The node travels with the lot so the raise goes back where it came from.
    router = room.LotRouter(
        read=lambda node: rtdb.read_snapshot(db, f"{node}/{league}"),
        write=lambda payload, node: rtdb.place_raise(db, league, payload, node=node),
    )

    def target_of(snapshot: Mapping[str, Any]) -> tuple[str, int] | None:
        import time as _time

        frame = tracker.cycle(snapshot, now_ms=int(_time.time() * 1000), node=router.node)
        latest.append(frame)
        # Said once rather than once per poll: at a 2 s cycle the same line would scroll the
        # heartbeat away inside a minute, and the heartbeat is all the operator is reading.
        if frame.note and frame.note not in reported:
            reported.add(frame.note)
            console.print(f"[yellow]{frame.note}[/yellow]")
        if frame.target is None or frame.walk_away is None:
            return None
        return (frame.target, frame.walk_away)

    def _remaining() -> int:
        return latest[-1].credits_left if latest else int(budget)

    def _cap() -> int:
        return latest[-1].max_cap if latest else max_bid(int(budget), RosterRules().size)

    report = room.run_bid_loop(
        seat=seat,
        fantaleague_id=league,
        remaining_budget=_remaining,
        max_cap=_cap,
        target_of=target_of,
        read=lambda: router.read_lot()[0],
        write=bid_writer(
            auto_act=settings.fantabot_auto_act,
            arm=arm,
            send=router.write_raise,
        ),
        now=lambda: int(time.time() * 1000),
        sleep=time.sleep,
        keep_going=lambda _cycle: True,
        heartbeat=console.print,
        poll_seconds=poll,
    )
    journal.close()
    _report_stopped(report)


#: `(name, function)`. Explicit, because the group supplies the prefix: the command
#: is `asta optimize`, not `asta asta optimize`.
COMMANDS: tuple[tuple[str, Callable[..., None]], ...] = (
    ("optimize", asta_optimize),
    ("legality", asta_legality),
    ("live", asta_live),
    ("bid", asta_bid),
    ("calibrate", asta_calibrate),
    ("room", asta_room),
)


def register(asta: typer.Typer) -> None:
    """Attach the asta commands to their group."""
    for name, command in COMMANDS:
        asta.command(name)(command)
