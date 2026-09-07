"""The plan solve runs once per state, not once per poll — and invalidates when it must.

The per-cycle re-plan is the loop's expensive act: `reservations(n_targets=None)` is one
roster solve per unowned plan member. At a 2 s poll a lot sits on the block for 20-60 s, so
without a memo the same answer was computed thirty times for one lot. The recorded
2026-09-01 evening's only gaps over 60 s are 72.3 / 181.0 / 61.1 / 63.0 s, and the 72 s one
is that re-plan, *inside* a run.

`RoomTracker.cycle` memoises on `(state, rules)` — the only inputs the solve reads. Nothing
else that moves per poll (`price`, `seconds_left`, `recent`, the high bidder) feeds it.

**This is a property test, not a benchmark.** `tests/domain/asta/test_asta_cycle_cost.py`
owns the cost — two call-count ceilings, and its own docstring argues why wall clock is the
wrong assertion. What is missing there, and is here, is whether the memo *holds*: a cost
ceiling passes just as happily when the solve runs every poll and is merely cheap, which is
the state this code was in for the evening that stalled.
"""

from __future__ import annotations

from typing import Any

from test_asta_room_tracker import _lot, _tracker

from fantabot.domain.asta import reservation
from fantabot.domain.asta.live import AssignmentEvent


def _counting(monkeypatch: Any) -> list[int]:
    """Count real `reservations` calls without replacing them — the answers must stay real,
    or the memo could be 'proved' by a stub that never disagrees with itself."""
    import fantabot.application.asta_room as room

    calls = [0]
    real = reservation.reservations

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(room, "reservations", spy)
    return calls


def test_a_lot_sitting_on_the_block_is_solved_once(monkeypatch: Any) -> None:
    """Thirty polls of one lot, one solve. This is the 72 s stall, removed."""
    calls = _counting(monkeypatch)
    tracker = _tracker()

    for poll in range(30):
        tracker.cycle(_lot(price=poll + 1), now_ms=1_000 + poll * 2_000)

    assert calls[0] == 1, f"the plan was re-solved {calls[0]} times for one unchanged state"


def test_the_price_moving_does_not_invalidate_it(monkeypatch: Any) -> None:
    """A rival raising is the commonest thing that changes between polls, and the solve
    does not read the price at all — `lot_ceiling` does, per lot, which is a different
    and much smaller thing."""
    calls = _counting(monkeypatch)
    tracker = _tracker()

    tracker.cycle(_lot(price=1), now_ms=1_000)
    tracker.cycle(_lot(price=99), now_ms=3_000)

    assert calls[0] == 1


def test_a_sale_invalidates_it(monkeypatch: Any) -> None:
    """The memo must not be a cache that never expires. `taken` and `owned` are what move,
    and both live on the state the key is built from."""
    calls = _counting(monkeypatch)
    sold: list[AssignmentEvent] = []
    tracker = _tracker(ledger=sold)

    tracker.cycle(_lot(), now_ms=1_000)
    sold.append(AssignmentEvent("uuid-a2", 39, "rival"))
    tracker.cycle(_lot(), now_ms=3_000)

    assert calls[0] == 2, "a sale left the plan solved against a board that had moved"


def test_and_the_new_answer_is_used_rather_than_the_cached_one(monkeypatch: Any) -> None:
    """A memo that invalidates and then serves the old value is worse than none: it would
    pass the call-count test above and still bid on a player somebody else owns."""
    sold: list[AssignmentEvent] = []
    tracker = _tracker(ledger=sold)

    before = tracker.cycle(_lot(), now_ms=1_000)
    assert "300" in before.plan or "200" in before.plan

    sold.append(AssignmentEvent("uuid-a1", 40, "rival"))
    after = tracker.cycle(_lot(uuid="uuid-a2"), now_ms=3_000)

    assert "200" not in after.plan, (
        "the plan still names a player the ledger says was sold to a rival"
    )
