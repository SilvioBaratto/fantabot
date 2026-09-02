"""Replay a recorded scenario through `RoomTracker`, socket-free. Pure.

**What this is for.** SPEC §8 items 2 and 3 ask for one thing a unit test cannot give:
proof that the *whole* room — the ledger fold, the plan solve, `lot_ceiling`, `decide_bid` —
agrees on a real evening's problem lots, not just that each piece is individually correct.
`tests/domain/asta/test_asta_reservation.py`'s `TestLotCeilingGeneralizesToTheLotOnTheBlock`
already proves `lot_ceiling` prices Malen; this proves `RoomTracker.cycle`, wired exactly as
`asta_room` wires it, would have journaled a real, non-`None` decision for him too.

**Every seam `RoomTracker` already takes is injected here, none of them opened.** `ledger` is
a fixed list built from a scenario's `our_purchases_before`; `journal` is a plain list append;
the poll loop drives `cycle` with the scenario's own recorded `(at_ms, price, team_id)` rows.
Nothing here reads a clock, a socket, or a database — the fixtures under
`tests/golden/asta_2026_09_01/` and `tests/golden/{quotazioni,sentiment,listone_map}.json(l)`
carry everything a replay needs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fantabot.application.asta_room import RoomTracker
from fantabot.domain.asta.bid import Seat
from fantabot.domain.asta.legality import SchemaLegality
from fantabot.domain.asta.live import AssignmentEvent
from fantabot.domain.asta.reservation import BARGAIN_BETA, BARGAIN_BUDGET_SHARE
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.asta.state import RosterRules
from fantabot.domain.asta.value import ValueModel

#: The seat the replay bids from. The ids are arbitrary — nothing is sent, and a scenario's
#: own recorded rungs (`Rung.team_id`) are compared against `OUR_TEAM_ID` below to tell our
#: own real historical raises apart from a rival's, not against this seat.
BENCH_SEAT = Seat(fantateam_id="__bench__", user_id="__bench__")

#: The real evening's own fantateam id ("è morto malen", 2026-09-01) — what a scenario's
#: `Rung.team_id` is compared against to decide whether a rung was genuinely our own raise.
OUR_TEAM_ID = "3097845d-6d44-42e9-9668-37803806036e"


@dataclass(frozen=True)
class Rung:
    """One recorded price on the block, at one instant. Pure data.

    `team_id` is `None` for a scenario with no recorded bidder identity (Ostigard and
    Malen — the harvester missed their window, so this is our own journal's price-per-poll,
    which never recorded who held the bid). Present and real for Vicario, whose full 59-rung
    ladder came from `asta_assignment.ladder`.
    """

    at_ms: int
    price: int
    team_id: str | None = None


@dataclass(frozen=True)
class BenchScenario:
    """One recorded lot, and the state we were really in when it was on the block.

    `our_purchases_before` is the real ledger fold up to the scenario's first rung —
    `(fantacalcio_id, price)` pairs summing to exactly what the real evening's journal
    recorded as spent at that moment (`tests/golden/asta_2026_09_01/*.json`'s own
    `_derived_from` field states the provenance and what, if anything, was synthesized).
    """

    name: str
    lot_uuid: str
    fantacalcio_id: str
    our_purchases_before: tuple[tuple[str, int], ...]
    rungs: tuple[Rung, ...]


def replay(
    scenario: BenchScenario,
    *,
    pool: Sequence[MantraPlayer],
    value: ValueModel,
    prices: Mapping[str, float],
    teams: Mapping[str, str],
    legality: dict[str, SchemaLegality],
    names: Mapping[str, str],
    bridge: Mapping[str, int],
    rules: RosterRules = RosterRules(),
    budget: float = 500.0,
    lam: float = 0.3,
    ceiling_alpha: float = 1.0,
    bargain_beta: float = BARGAIN_BETA,
    bargain_share: float = BARGAIN_BUDGET_SHARE,
) -> list[Mapping[str, object]]:
    """Drive `RoomTracker.cycle` over one scenario's recorded rungs. Pure, opens nothing.

    Returns the journal rows `RoomTracker` itself builds — the same shape `RoomJournal`
    writes to disk in a live room — not `RoomFrame`s, so the output is diffable line-for-line
    against a real evening's `room_journal.jsonl` (SPEC §8 item 3).

    `bargain_beta`/`bargain_share` default to the *domain* defaults, not the CLI's
    conservative-by-design `0.00` (`interface/options.py`) — the bench exists to demonstrate
    what the fixed mechanism can do when it runs, the same reason `test_asta_bargain.py`
    exercises it directly rather than through a command line an operator has to opt into.

    A rung's `team_id` becomes the snapshot's `user_id` only when it equals `OUR_TEAM_ID` —
    `decide_bid` refuses to bid against its own high bid (`already_high`), and a replay that
    fed our own uuid as every rung's bidder would refuse every single one for the wrong
    reason. Every other rung is a generic `"rival"`, since `decide_bid`'s guard only cares
    whether *we* are already high, never who else is.
    """
    reverse_bridge = {str(fid): uuid for uuid, fid in bridge.items()}
    ledger = [
        AssignmentEvent(reverse_bridge[fid], price, OUR_TEAM_ID)
        for fid, price in scenario.our_purchases_before
        if fid in reverse_bridge
    ]

    rows: list[Mapping[str, object]] = []
    tracker = RoomTracker(
        seat=BENCH_SEAT,
        bridge=dict(bridge),
        pool=pool, value=value, prices=prices, teams=teams, legality=legality, names=names,
        rules=rules,
        budget=budget,
        lam=lam,
        ceiling_alpha=ceiling_alpha,
        bargain_beta=bargain_beta,
        bargain_share=bargain_share,
        ledger=lambda: list(ledger),
        journal=rows.append,
        counter_time=10,
        counter_time_first=20,
    )
    for rung in scenario.rungs:
        snapshot = {
            "player_id": scenario.lot_uuid,
            "price": rung.price,
            "user_id": BENCH_SEAT.user_id if rung.team_id == OUR_TEAM_ID else "rival",
            "last_bid_time": rung.at_ms - 3_000,
        }
        tracker.cycle(snapshot, now_ms=rung.at_ms)
    return rows
