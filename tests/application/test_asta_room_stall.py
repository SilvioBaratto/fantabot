"""A lot change no longer stalls the loop — measured on the evening that stalled.

`tasks/todo.md` 1.9's acceptance is *"a lot change no longer stalls past the poll interval,
**measured on the recorded corpus**"*. The unit tests beside this one prove the memo *holds*
across polls of one lot; what they cannot say is what the expensive cycle — the one where the
memo misses because the board moved — actually costs against a real evening.

The corpus is `data/room_journal.jsonl`: 5,192 rows from 2026-09-01, 474 lot changes. Its
only four gaps over 60 s are **181.0 / 72.3 / 63.0 / 61.1 s**, and the 72.3 s one is this
stall, inside a run.

**The sample is bounded and the bound is stated.** Every lot change would be 460 memo-miss
solves at ~0.1 s each — 45 s of test time in a tier that runs in seventeen. `SAMPLE` lot
changes are replayed instead, in the order the evening called them. That is enough because
the margin is not marginal: **measured 2026-09-07, max 0.275 s and median 0.076 s over the
first twelve** — 263x faster than the 72.3 s stall, with 7x headroom against the 2 s poll.

Wall clock, not call count — deliberately, and against `test_asta_cycle_cost.py`'s own
argument for the opposite. That file guards a *reversal* and is right to count calls. This
one answers "does the operator wait", which is a question about seconds, so the threshold is
set 10x above the measurement rather than snugly, and it fails only on a collapse.
"""

from __future__ import annotations

import json
import time

import pytest
from _golden import (
    PINNED_TODAY,
    load_clearing_sales,
    load_listone_bridge,
    load_quotazioni,
    load_sentiment,
)
from _paths import REPO

from fantabot.application.asta_room import RoomTracker
from fantabot.domain.asta.bid import Seat
from fantabot.domain.asta.live import AssignmentEvent
from fantabot.domain.asta.state import RosterRules

#: `asta bid`'s default (`interface/asta.py`), and the number the criterion is about.
POLL_SECONDS = 2.0

#: 10x the poll interval. A ceiling that sits at the measurement flakes on a loaded laptop
#: and gets deleted; this one fails on a collapse and on nothing else. The stall it replaces
#: was 72.3 s, which is 36x the poll interval — well the wrong side of this line.
CEILING_SECONDS = POLL_SECONDS * 10

#: How many of the evening's 474 lot changes are replayed. Stated, not silent: a bound that
#: truncates without saying so reads as "we measured the whole evening".
SAMPLE = 12

JOURNAL = REPO / "data" / "room_journal.jsonl"


def _recorded_lot_changes() -> list[tuple[str, int]]:
    """`(lot_uuid, price)` each time the block changed, in the order the room called them."""
    changes: list[tuple[str, int]] = []
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        lot = row.get("lot")
        if lot and (not changes or changes[-1][0] != lot):
            changes.append((str(lot), int(row.get("price") or 1)))
    return changes


@pytest.fixture(scope="module")
def world():  # type: ignore[no-untyped-def]
    from fantabot.application.asta_planner import build_plan_inputs
    from fantabot.domain.asta.prices import Sale, mean_prices

    return build_plan_inputs(
        load_quotazioni(),
        mean_prices(Sale(pid, price) for pid, price in load_clearing_sales()),
        load_sentiment(),
        as_of=PINNED_TODAY,
        tilt_k=0.25,
    )


def test_the_recorded_evening_is_the_corpus_this_measures() -> None:
    """A scan over a missing file examines nothing and passes — `_paths.pkgs`'s reason, and
    the exact shape of the gate that judged fixtures for a week."""
    assert JOURNAL.is_file(), f"{JOURNAL} is gone; this test would measure nothing"
    changes = _recorded_lot_changes()

    assert len(changes) > 400, f"only {len(changes)} lot changes — is this the right file?"


def test_a_lot_change_does_not_stall_past_the_poll_interval(world) -> None:  # type: ignore[no-untyped-def]
    """Every cycle here is a memo *miss*: the ledger grows by one sale before each, which is
    what a lot change is to the tracker. That is the expensive cycle, and it is the one the
    72.3 s gap came from."""
    bridge = load_listone_bridge()
    changes = [(lot, price) for lot, price in _recorded_lot_changes() if lot in bridge]
    assert len(changes) >= SAMPLE, f"only {len(changes)} resolvable lots in the recording"

    ledger: list[AssignmentEvent] = []
    tracker = RoomTracker(
        seat=Seat(fantateam_id="us", user_id="me"),
        bridge=bridge,
        pool=world.pool, value=world.value, prices=world.prices, teams=world.teams,
        legality=world.legality, names=world.names,
        rules=RosterRules(),
        budget=500.0,
        lam=0.3,
        ledger=lambda: list(ledger),
        journal=lambda _row: None,
        counter_time=10, counter_time_first=20,
    )

    worst = 0.0
    slowest = ""
    for index, (lot, price) in enumerate(changes[:SAMPLE]):
        # The previous lot closes to a rival: the board moved, so the plan must re-solve.
        if index:
            gone, went_for = changes[index - 1]
            ledger.append(AssignmentEvent(gone, max(1, went_for), "rival"))
        started = time.perf_counter()
        tracker.cycle(
            {"player_id": lot, "price": price, "user_id": "rival", "last_bid_time": 0},
            now_ms=1_000 + index * 2_000,
        )
        elapsed = time.perf_counter() - started
        if elapsed > worst:
            worst, slowest = elapsed, lot

    assert worst < CEILING_SECONDS, (
        f"the slowest of {SAMPLE} recorded lot changes took {worst:.2f}s (lot {slowest}), "
        f"over the {CEILING_SECONDS:.0f}s ceiling. The stall this replaced was 72.3s."
    )
    # The real bar, and the one the criterion names. Kept separate from the ceiling above so
    # a failure says which line was crossed: a cycle slower than the poll is a loop falling
    # behind the room, even if it is nowhere near a stall.
    assert worst < POLL_SECONDS, (
        f"a lot change took {worst:.2f}s, past the {POLL_SECONDS}s poll interval — the loop "
        "is behind the room at a lot change, which is exactly what 1.9 is about"
    )
