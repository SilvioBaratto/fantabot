"""One live room, composed once: the tracker, and the one poll both surfaces drive it through.

`RoomTracker` was already here; its **construction** was not. Twenty keywords were assembled
inside `asta room`'s Typer body, and the app's room route would have assembled them again from
the same `ResolvedRoom` and the same `PlanInputs` — which is the drift `CLAUDE.md` records
twice over, once as three commands each growing their own value model and once as
`GET /asta/plan` building a plan differing from `asta optimize`'s in ten inputs. None of those
keywords raises when it is dropped: a missing `seat_by_user` silently stops attributing our own
passed lots, a missing `admin_user_id` silently claims 248 of somebody's auto-skips, and a
missing `counter_time` leaves the screen with no countdown. A second copy is not a second
opinion, it is a slow divergence nobody is told about.

**The paint stays in the interface.** `render`, `listone_rows`, `error_overlay` and the `Live`
are the CLI's; so is the clock — `cycle_ms` is measured around this call, not inside it, for the
same reason `interface/asta.py::_today` is the asta feature's only calendar read. What crosses
is the decision: the frame, and the `(target, walk_away)` tuple `run_bid_loop` asks a
`target_of` for.

**The loop is here for the same reason the cycle is.** `run_bid_loop`'s wiring, the `latest`
one-slot buffer its budget and cap guards read, the heartbeat line that journals a poll
`RoomTracker.cycle` never ran, and the error row a failed poll leaves behind were all in the
same Typer body — and the gap that found was that **nothing in 2,079 tests noticed the body
dropping the session's answer**: forcing its `target_of` to return `None` left the whole suite
green, and the room would have watched all evening and never bid. The decision and the loop
that acts on it are one call now, so there is no seam left to drop it at.

What still crosses outward is paint and the clock: `on_frame` is handed each frame and
`on_error` each failure, and both are the caller's to draw. `cycle_ms` is measured around the
`journal` this module is given, not inside it.

This module opens nothing and can prove it: `test_asta_session.py` asserts it reaches neither
Postgres, nor Rich, nor typer, nor `interface/`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from fantabot.adapters.files.stopflag import EXIT
from fantabot.adapters.http.fantalab import rtdb
from fantabot.adapters.http.fantalab.listone import is_stale
from fantabot.adapters.http.fantalab.room import LoopReport, LotRouter, run_bid_loop
from fantabot.application.arming import Arming, decide_arming
from fantabot.application.asta_room import (
    ResolvedRoom,
    RoomFrame,
    RoomRefused,
    RoomRules,
    RoomTracker,
    error_row,
    waiting_row,
)
from fantabot.application.plan_inputs import PlanInputs
from fantabot.domain.asta.bid import Seat
from fantabot.domain.asta.live import AssignmentEvent

Snapshot = Mapping[str, Any]

#: The third lock, by **name**, beside `arming.AUTO_ACT` and `arming.ARM`. A name rather than
#: a sentence for the reason that module gives: the fact is shared by both surfaces, and the
#: wording is not — this one's CLI line carries the measured age and the operator's own limit,
#: which no static map can hold.
STALE_BRIDGE = "listone-bridge"


@dataclass(frozen=True)
class Cycle:
    """What one poll decided, in the two shapes its two callers need.

    `frame` is everything a screen draws. `target` is the same frame's decision narrowed to
    what `run_bid_loop` accepts — and narrowing it is a decision, not a projection: a target
    with no price is not a target, because a raise against a `None` ceiling is unbounded.
    """

    frame: RoomFrame
    target: tuple[str, int] | None


class AstaSession:
    """One room's tracker, and the poll both surfaces drive it through.

    A class rather than a closure so the app can hold one across requests: the tracker carries
    the evening's memory — the bargain wins, the uuids already known unresolvable, the last
    bridge refresh — and a session rebuilt per poll would hand every cap back its allowance and
    re-fetch the listone for each unmappable lot.
    """

    def __init__(
        self,
        tracker: RoomTracker,
        *,
        journal: Callable[[Mapping[str, Any]], None],
        seat: Seat,
        fantaleague_id: str,
    ) -> None:
        self._tracker = tracker
        #: The same sink `RoomTracker` writes its own row through. Held here too because the
        #: two rows that mean the loop is in trouble are built *outside* the tracker: on a
        #: poll with no lot, `run_bid_loop` short-circuits before `cycle`, so nothing inside
        #: `RoomTracker` ever sees it.
        self._journal = journal
        #: Assembled once, in `session_for`, from the room's chair and the stored uid.
        #: `run_bid_loop` needs the same pair, and the Typer body used to build a second
        #: `Seat(...)` for it out of the same two fields — two chances to swap the ids, which
        #: is a `200` that drives somebody else's team all evening.
        self._seat = seat
        self._fantaleague_id = fantaleague_id

    @property
    def tracker(self) -> RoomTracker:
        """For the callers that still reach past the session. Read-only on purpose."""
        return self._tracker

    def cycle(
        self, snapshot: Mapping[str, Any] | None, *, now_ms: int, node: str = "auction"
    ) -> Cycle:
        """One poll: fold, re-plan, decide, journal — and both answers."""
        frame = self._tracker.cycle(snapshot, now_ms=now_ms, node=node)
        return Cycle(frame=frame, target=target_of(frame))

    def run(
        self,
        *,
        node: Callable[[], str],
        read: Callable[[], Snapshot | None],
        write: Callable[[dict[str, Any]], Any],
        now: Callable[[], int],
        sleep: Callable[[float], None],
        on_frame: Callable[[RoomFrame], None],
        on_error: Callable[[Exception, int], None],
        fallback_budget: int,
        fallback_cap: int,
        poll_seconds: float,
        keep_going: Callable[[int], bool] = lambda _cycle: True,
        on_heartbeat: Callable[[str], None] = lambda _line: None,
    ) -> LoopReport:
        """Poll the room until `keep_going` says otherwise, bidding this session's own targets.

        Every effect is injected, exactly as `run_bid_loop` takes them, so this opens nothing:
        `read` and `write` are the caller's bound RTDB calls, `now` and `sleep` are its clock,
        and `on_frame` / `on_error` are its paint. The clock is a parameter rather than a call
        for the reason `interface/asta.py::_today` exists — the asta feature reads the calendar
        in exactly one place, and it is not this layer.

        **`node` is a callable, not a string.** `LotRouter.node` is rewritten by every
        `read_lot()`: CHIAMA puts a lot on `auction/<fl>` and ASSEGNA on `assign/<fl>`, and a
        raise must go back to the node its lot came from. A node read once at composition is
        the node the room happened to be using before the first poll.

        **`write` is called, not composed.** It must re-read the arming flag per bid — that is
        what makes a Ctrl-C hold the very next raise — so a caller passes a closure over its
        own `armed` list rather than a writer bound at loop start. The gate itself
        (`bid_writer`) stays with the surface that owns the two locks.

        `fallback_cap` is an `int` and not an optional, although `run_bid_loop` accepts `None`
        for "no cap": the cap guard is the only thing between a "pay anything" walk-away and a
        rosa that cannot be fielded, and a session that could be composed without one is a
        session somebody composes without one.

        `fallback_budget` and `fallback_cap` are only what the guards read **before the first
        frame exists**. From the first poll on they read the frame, because `remaining_budget`
        was once a plain int passed once and after the first lot won it compared every bid
        against a number that had stopped being true.
        """
        #: One slot, not a log: only the last frame is ever read, and a frame per poll for
        #: three hours is thousands of walk-away dicts held by a process that must not die
        #: mid-auction.
        latest: list[RoomFrame] = []

        def pick(snapshot: Snapshot) -> tuple[str, int] | None:
            cycle = self.cycle(snapshot, now_ms=now(), node=node())
            latest[:] = [cycle.frame]
            on_frame(cycle.frame)
            return cycle.target

        def heartbeat(line: str) -> None:
            """Journaled for one line; shown by whoever has somewhere to show it.

            `run_bid_loop` writes the waiting message only when `read()` returned no lot at
            all: the one poll where `cycle` (and so the journal row it writes itself) never
            ran. Journaling any other line would double the row for the same poll.

            **`on_heartbeat` defaults to a no-op because one surface has no use for it and
            the other has nothing else.** `asta room` paints a Rich `Live` and the frame *is*
            the screen, so a scrolling line would fight it; `asta bid` prints, and the
            heartbeat is the whole of what an operator watching an armed run can read. A lift
            that journaled the line and stopped printing it would leave that run with a blank
            terminal for as long as the room stayed empty.
            """
            on_heartbeat(line)
            if "waiting for a lot" in line:
                self._journal(waiting_row(now_ms=now()))

        def failed(exc: Exception, consecutive: int) -> None:
            """Journaled here and painted by the caller. A run reporting a stall used to
            leave no record of why.

            Passed to `run_bid_loop` rather than left to its own fallback, which only ever
            printed: the alternative the room used — sniffing the heartbeat line's *text* for
            "Error"/"timed out" — silently missed `ReadTimeout`, `ConnectTimeout` and
            `PoolTimeout`, on a flaky link the three most likely of all.
            """
            self._journal(error_row(exc, now_ms=now()))
            on_error(exc, consecutive)

        return run_bid_loop(
            seat=self._seat,
            fantaleague_id=self._fantaleague_id,
            remaining_budget=lambda: latest[-1].credits_left if latest else fallback_budget,
            max_cap=lambda: latest[-1].max_cap if latest else fallback_cap,
            target_of=pick,
            read=read,
            write=write,
            now=now,
            sleep=sleep,
            keep_going=keep_going,
            heartbeat=heartbeat,
            on_error=failed,
            poll_seconds=poll_seconds,
        )


def lot_router(db: int | None, fantaleague_id: str) -> LotRouter:
    """The room's two nodes, bound to one shard. The **only** place either surface builds one.

    Both live commands assembled this from the same two fields, and it is the last thing
    `interface/` did that could write: `tests/test_layers.py`'s T-spine rule — *"no module
    under `interface/` may name a **writing** call from `apileague.py` or `rtdb.py`"* — kept
    `place_raise` on its ratchet for exactly this, and the ratchet is empty now.

    What the rule is really about is the *other* copy. Two bodies binding `rtdb.place_raise`
    to a shard is two chances to bind it to the wrong one, and a raise sent to another room's
    shard is a `200` that does nothing while the operator watches a lot they think they are
    bidding on.

    The write is bound unconditionally and the arming gate is **not** here: `bid_writer` stays
    with the surface that owns the two locks, and the router is handed to it, never the other
    way round. A router that knew about arming would be a second lock nobody turned.
    """
    if db is None:
        # `ResolvedRoom.db` is optional because the platform's own field is, and a room with
        # no shard cannot be addressed at all — every read and every raise is keyed by it.
        # Refused by name here rather than left to a `TypeError` five calls into the adapter:
        # this is a room that cannot be driven, which is a sentence an operator can act on.
        raise RoomRefused(
            f"room {fantaleague_id} declares no RTDB shard — it cannot be read or bid in."
        )
    shard = db
    return LotRouter(
        read=lambda node: rtdb.read_snapshot(shard, f"{node}/{fantaleague_id}"),
        write=lambda payload, node: rtdb.place_raise(shard, fantaleague_id, payload, node=node),
    )


def stop_poll(
    *,
    read_stage: Callable[[], str | None],
    armed: list[bool],
    announce: Callable[[str], None],
) -> Callable[[int], bool]:
    """A `keep_going` that honours the polled two-stage stop. Pure — every effect injected.

    **Why a file and not a signal**: `stopflag.py`'s docstring is the diagnosis. On Windows
    `CTRL_BREAK_EVENT` reaches a Python child as SIGBREAK and terminates it before
    `except KeyboardInterrupt` runs, so `ProcessJob._signal` sends nothing there at all and
    the flag is the *whole* stop. Neither live command ever read one, which for a watch is
    harmless — the journal flushes per line — and for a bidder is the difference between
    "stop bidding" and "keep bidding until the grace timer kills you".

    **Two stages, because the gesture has two.** *Disarm* clears the writer's lock and keeps
    the loop running: the room still draws, which is the reason the gesture is not a
    boolean — blanking the screen takes the walk-away away at the exact moment the operator
    has to bid by hand. *Exit* clears it too and ends the loop. There is no stage after
    *exit*; a caller that wants more escalates by killing the process, which is a different
    mechanism on purpose.

    **A run with nothing to disarm leaves on the first request**, which is `_disarm_on_sigint`'s
    own rule and has to be this one too, or the two platforms stop agreeing. On POSIX
    `ProcessJob.stop` sends a `SIGINT` *as well as* writing the flag, and a never-armed run
    ends there on the first click; on Windows nothing is sent, so without this the same click
    would disarm a run that was already disarmed and keep it alive — `ProcessJob.stop`'s own
    note, "it has nothing to disarm and winds down on either stage", true on one platform
    only. §12's second success criterion is that a stop works the same on both.

    ⚠ **"Nothing to disarm" is a fact about the *start* of the run, and reading `armed` for it
    is a real defect.** Both mechanisms clear the same list, and on POSIX both arrive for one
    click: `_request_stop` writes the flag, `_signal` sends the `SIGINT`, the handler clears
    `armed[0]` and keeps the run drawing — and the next poll then reads that same `disarm` off
    disk. A gate asking "is it armed *now*" finds `False`, takes it for "nothing to disarm",
    and **ends an armed run on its first Stop, on POSIX only** — which is exactly the
    divergence this rule exists to close, reintroduced by the rule. So the answer is snapshot
    once, here, before the loop and before any handler can have fired.

    **`armed` is the same list `_disarm_on_sigint` clears and the writer reads per bid.** One
    disarm, two ways to ask for it — a second flag would be a second answer to "is this run
    armed", and the two would disagree the first time both were used.

    **The flag stays on disk, so a stage is read on every poll after it is written** —
    `request_stop` escalates only when the *caller* asks again. That is what `honoured` is
    for, and it is load-bearing rather than cosmetic: without it the second read of one
    `disarm` would find `armed` already false, take that for "nothing to disarm" and end a
    run the operator asked to keep watching. One request, honoured once. It also keeps the
    line to one: at a 2 s cadence the same sentence scrolls the heartbeat away inside a
    minute, and under a supervisor every line is a row in the job log.
    """
    #: Read once, at composition, which is before the loop and before any handler can have
    #: fired. See the ⚠ above: this is the whole of why it is not read per poll.
    armed_at_start = armed[0]
    honoured = False

    def keep_going(_cycle: int) -> bool:
        nonlocal honoured
        stage = read_stage()
        if stage is None:
            return True
        if stage == EXIT:
            armed[0] = False
            announce("stop requested — leaving")
            return False
        if honoured:
            return True
        honoured = True
        armed[0] = False
        if not armed_at_start:
            announce("stop requested — leaving")
            return False
        announce("stop requested — disarmed, still watching")
        return True

    return keep_going


def target_of(frame: RoomFrame) -> tuple[str, int] | None:
    """The frame as `run_bid_loop` wants it: the player, and the most we will pay for him.

    Both halves are required, and the second one is a **type** guard rather than a runtime
    one — worth saying, because it is the half a later reader would delete as redundant.
    `RoomTracker._decide` sets `target` and `walk_away` in the same `RoomFrame` call every
    time, so no frame it builds has one without the other; the mutation that weakens this to
    `and` passes all thirteen tests in `test_asta_session.py` and is caught by **mypy**, which
    stops being able to type the tuple as `tuple[str, int]`. That is the check to keep green
    if this line is ever touched.

    It is still written as a refusal rather than an `assert`, because the frame is a value
    another caller may one day build: handing the loop a target with a `None` ceiling is a
    raise against no ceiling at all, and `MIN_BID`-floored pricing produced exactly that for
    10 of 30 measured players (`CLAUDE.md`, defect B2) while the same plan budgeted 96 credits
    for one of them.
    """
    if frame.target is None or frame.walk_away is None:
        return None
    return (frame.target, frame.walk_away)


def session_from(
    *,
    seat: Seat,
    fantaleague_id: str,
    admin_user_id: str | None,
    seat_by_user: Mapping[str, str] | None,
    counter_time: int | None,
    counter_time_first: int | None,
    bridge: Mapping[str, int],
    world: PlanInputs,
    rules: RoomRules,
    budget: float,
    lam: float,
    ceiling_alpha: float,
    bargain_beta: float,
    bargain_share: float,
    ledger: Callable[[], Iterable[AssignmentEvent]],
    journal: Callable[[Mapping[str, Any]], None],
    bridge_refresh: Callable[[], Mapping[str, int]] | None = None,
) -> AstaSession:
    """The one construction. Everything the tracker needs, named by the caller that has it.

    `session_for` is this with a `ResolvedRoom` read for it; **`asta bid` cannot use that
    door**, because it is unauthenticated by design (its own docstring) and never fetches
    `RoomConfig` — so the four room-shaped facts arrive here as `None` rather than through a
    room the command cannot see. Two entry points, one composition: the alternative was the
    bidder keeping the twenty-keyword assembly it already had, which is the divergence this
    module exists to prevent and the one that historically fell behind on the surface that
    spends credits.

    **The four are required keywords, not defaults.** Each degrades *silently* when it is
    missing — a missing `seat_by_user` stops attributing our own passed lots, a missing
    `admin_user_id` claims somebody else's auto-skips, a missing `counter_time` leaves the
    screen with no countdown — and `RoomTracker` already defaults all four. A second default
    here would be the second copy, one layer up. A caller with nothing to give says `None`
    out loud.

    The five numbers are required for the same reason: both surfaces read them from their own
    option set. `bridge_refresh` is the genuine optional — the listone endpoint is
    unauthenticated, so even `asta bid` has one, but a caller composing a session for a replay
    has nothing to fetch with.
    """
    tracker = RoomTracker(
        seat=seat,
        bridge=bridge,
        pool=world.pool,
        value=world.value,
        prices=world.prices,
        teams=world.teams,
        legality=world.legality,
        names=world.names,
        rules=rules,
        budget=budget,
        lam=lam,
        ceiling_alpha=ceiling_alpha,
        bargain_beta=bargain_beta,
        bargain_share=bargain_share,
        admin_user_id=admin_user_id,
        seat_by_user=seat_by_user,
        bridge_refresh=bridge_refresh,
        ledger=ledger,
        journal=journal,
        counter_time=counter_time,
        counter_time_first=counter_time_first,
    )
    return AstaSession(
        tracker,
        journal=journal,
        seat=seat,
        fantaleague_id=fantaleague_id,
    )


def session_for(
    *,
    resolved: ResolvedRoom,
    user_id: str,
    bridge: Mapping[str, int],
    world: PlanInputs,
    rules: RoomRules,
    budget: float,
    lam: float,
    ceiling_alpha: float,
    bargain_beta: float,
    bargain_share: float,
    ledger: Callable[[], Iterable[AssignmentEvent]],
    journal: Callable[[Mapping[str, Any]], None],
    bridge_refresh: Callable[[], Mapping[str, int]] | None = None,
) -> AstaSession:
    """Compose a session from the room the platform declared and the world we read.

    The authenticated door. Everything it adds over `session_from` is read off the one
    `ResolvedRoom`: the chair, the admin uid, the chair table and the two countdown values.
    Nothing is decided here — this is the translation, and keeping it a translation is what
    lets the unauthenticated bidder share the composition rather than grow a second one.

    **`user_id` is separate from `resolved.seat`.** Two `Seat` types are in play (`asta_room`'s
    own note): the room's chair carries a team name and a position, the bid payload carries a
    pair of ids. The seat's `user_id` is `None` for a free chair, and ours comes from the stored
    FantaLab session — so the pair is assembled here, once, rather than in each caller.
    """
    return session_from(
        # Bound once and used twice — the tracker decides with it and `run_bid_loop` signs the
        # raise with it. Two constructions from the same two fields is two chances to swap them.
        seat=Seat(fantateam_id=resolved.seat.fantateam_id, user_id=user_id),
        fantaleague_id=resolved.fantaleague_id,
        admin_user_id=resolved.admin_id,
        seat_by_user=resolved.seat_by_user,
        counter_time=resolved.counter_time,
        counter_time_first=resolved.counter_time_first,
        bridge=bridge,
        world=world,
        rules=rules,
        budget=budget,
        lam=lam,
        ceiling_alpha=ceiling_alpha,
        bargain_beta=bargain_beta,
        bargain_share=bargain_share,
        ledger=ledger,
        journal=journal,
        bridge_refresh=bridge_refresh,
    )


def room_arming(
    *,
    arm: bool,
    auto_act: bool | None = None,
    bridge_age: float | None,
    max_bridge_age_hours: float,
) -> Arming:
    """The arming contract for a live room: the two locks, plus the bridge that names the lots.

    A third lock rather than a separate refusal, because it fails the same way and an operator
    fixes it in the same breath — and because `Arming.closed` names **every** shut lock, which
    a second return value would not. `decide_arming`'s own note is the reason: report one of
    two causes and an operator fixes it, retries, and is told about the other.

    **It refuses arming, never the run.** The room still draws and can be watched, exactly as
    a disarmed run always could — a stale bridge costs us the confidence to spend credits, not
    the ability to look at the evening. There is no third return value and no exception here;
    the only thing this can say is `armed is False`.

    A bridge with **no** age (a pre-envelope cache, or a fetch that never ran) is not stale:
    there is nothing to compare, and refusing on information a room never had the chance to
    write would punish the upgrade itself. `is_stale` is the adapter's, imported rather than
    restated — the staleness rule and the cache that measures the age belong together.
    """
    decision = decide_arming(arm=arm, auto_act=auto_act)
    if not is_stale(bridge_age, max_hours=max_bridge_age_hours):
        return decision
    # Last, not first: the two locks are things the operator *chose*, and this one is a fact
    # about the world they now have to react to. Fix order, same as `arming.decide_arming`'s.
    return Arming(armed=False, closed=(*decision.closed, STALE_BRIDGE))
