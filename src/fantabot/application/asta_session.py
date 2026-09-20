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

This module opens nothing and can prove it: `test_asta_session.py` asserts it reaches neither
Postgres, nor Rich, nor typer, nor `interface/`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from fantabot.application.asta_room import ResolvedRoom, RoomFrame, RoomRules, RoomTracker
from fantabot.application.plan_inputs import PlanInputs
from fantabot.domain.asta.bid import Seat
from fantabot.domain.asta.live import AssignmentEvent


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

    def __init__(self, tracker: RoomTracker) -> None:
        self._tracker = tracker

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


def target_of(frame: RoomFrame) -> tuple[str, int] | None:
    """The frame as `run_bid_loop` wants it: the player, and the most we will pay for him.

    Both halves are required. A target with no walk-away is what `MIN_BID`-floored pricing
    used to produce for 10 of 30 measured players (`CLAUDE.md`, defect B2), and handing that
    to the loop as a target with a `None` ceiling is how a plan that budgeted 96 credits for
    one of them would have bid without one.
    """
    if frame.target is None or frame.walk_away is None:
        return None
    return (frame.target, frame.walk_away)


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

    The five numbers are required keywords rather than defaulted: both surfaces read them from
    their own option set, and a default declared here as well as in `RoomTracker` is the second
    copy this module exists to prevent. `bridge_refresh` is the exception and stays optional —
    `asta bid` is unauthenticated by design and has no listone fetcher to give.

    **`user_id` is separate from `resolved.seat`.** Two `Seat` types are in play (`asta_room`'s
    own note): the room's chair carries a team name and a position, the bid payload carries a
    pair of ids. The seat's `user_id` is `None` for a free chair, and ours comes from the stored
    FantaLab session — so the pair is assembled here, once, rather than in each caller.
    """
    tracker = RoomTracker(
        seat=Seat(fantateam_id=resolved.seat.fantateam_id, user_id=user_id),
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
        admin_user_id=resolved.admin_id,
        seat_by_user=resolved.seat_by_user,
        bridge_refresh=bridge_refresh,
        ledger=ledger,
        journal=journal,
        counter_time=resolved.counter_time,
        counter_time_first=resolved.counter_time_first,
    )
    return AstaSession(tracker)
