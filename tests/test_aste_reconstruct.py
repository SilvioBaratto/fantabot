"""Turning a sequence of merged auction states into assignments.

The two input paths converge here. `data/aste_live/*.jsonl` holds merged states,
already collapsed by the poller; the live SSE path reduces frames into states of
the same shape. Neither reaches this module as frames, which is why the recorded
evening verifies *this* and not the reducer.

Two tiers of check. The committed fixture is one small auction and always runs.
The full evening is 80 MB, git-ignored by project policy, and skips when absent —
it is the regression that matters most and the one a fresh clone cannot perform.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fantabot.aste.reconstruct import reconstruct

FIXTURE = Path(__file__).parent / "fixtures" / "states" / "one_auction.jsonl"
EVENING = Path(__file__).parent.parent / "data" / "aste_live" / "events_2026-08-26.jsonl"

# Ground truth from scripts/resolve_aste_live.py, the poller-era resolver, run
# against this exact file on 2026-08-27. This module must not silently disagree
# with it: a difference means one of the two is wrong, and it has to be explained
# rather than absorbed.
#
# The input is pinned alongside the output. An earlier revision asserted 11,453 —
# correct when measured at 01:23 UTC, stale by 02:23 because the collector kept
# running for another hour. Without the line count, that failure reads as "the
# reconstruction broke" when it means "the input grew". With it, the message says
# which.
EVENING_LINES = 144_518
EVENING_ASSIGNMENTS = 11_498
FIXTURE_ASSIGNMENTS = 18


def _states(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def test_the_fixture_auction_reconstructs() -> None:
    assignments = reconstruct(_states(FIXTURE))
    assert len(assignments) == FIXTURE_ASSIGNMENTS


def test_every_assignment_names_a_player_and_a_price() -> None:
    for a in reconstruct(_states(FIXTURE)):
        assert a.player_id
        assert a.price >= 0, "a price is never negative; zero is a real clearing price"


def test_an_assignment_carries_the_ladder_that_produced_it() -> None:
    """The clearing price alone is what polling could already give us. The ladder
    is the reason this phase exists, so its absence must fail a test rather than
    be noticed later."""
    assignments = reconstruct(_states(FIXTURE))
    contested = [a for a in assignments if a.price > 1]
    assert contested, "the fixture has no contested player; pick a richer one"
    assert any(len(a.ladder) > 1 for a in contested), "no ladder was reconstructed"
    for a in contested:
        prices = [bid.price for bid in a.ladder]
        assert prices == sorted(prices), "a ladder must not step downwards"


def test_replaying_the_same_states_twice_changes_nothing() -> None:
    """A restarted collector re-emits every current state. Those duplicates must
    be absorbed, not counted — eleven restarts happened on 2026-08-26 alone."""
    states = _states(FIXTURE)
    once = reconstruct(states)
    twice = reconstruct(states + states)
    assert len(twice) == len(once)


def test_states_from_several_auctions_do_not_bleed_together() -> None:
    states = _states(FIXTURE)
    relabelled = [{**s, "auction_id": "other", "state": {**s["state"], "fantaleague_id": "other"}}
                  for s in states]
    both = reconstruct(states + relabelled)
    assert len(both) == 2 * len(reconstruct(states))
    assert len({a.auction_id for a in both}) == 2


@pytest.mark.skipif(not EVENING.exists(), reason="the recorded evening is not on this machine")
def test_the_recorded_evening_matches_the_poller_era_resolver() -> None:
    states = _states(EVENING)
    assert len(states) == EVENING_LINES, (
        f"the recorded evening has {len(states)} states, not the {EVENING_LINES} these "
        "numbers were measured against. The file is meant to be immutable; if it grew, "
        "re-derive EVENING_ASSIGNMENTS with scripts/resolve_aste_live.py before touching "
        "this module."
    )
    assert len(reconstruct(states)) == EVENING_ASSIGNMENTS


def test_an_auction_first_seen_between_players_does_not_crash() -> None:
    """A `confirm` state carries no player_id: the slot is empty between sales.
    If collection starts there, `None` is the player on the block — which is not
    the same as never having seen the auction. Conflating the two raised
    KeyError on the next raise, and a live capture that began mid-turn found it
    where the recorded evening never could: that file always starts on a
    first_call."""
    rows = [
        {"auction_id": "a-1", "state": {"update_type": "confirm", "last_update": 1}},
        {"auction_id": "a-1", "state": {"update_type": "raise", "price": 5, "last_update": 2}},
        {"auction_id": "a-1", "state": {"update_type": "first_call", "player_id": "p",
                                        "price": 0, "last_update": 3}},
        {"auction_id": "a-1", "state": {"update_type": "close_auction", "player_id": "p",
                                        "price": 9, "last_update": 4}},
    ]
    (assignment,) = reconstruct(rows)
    assert assignment.price == 9
    assert [b.price for b in assignment.ladder] == [0, 9], "the pre-turn raise must not leak in"


def test_a_recalled_player_starts_a_fresh_ladder() -> None:
    """`first_call` begins a turn, and a turn can begin twice for one player.

    Observed in auction `ccdbe75d` on 2026-08-26: bidding climbed to 17, the
    call was annulled, and a second `first_call` put the same player back on the
    block at 0, where he sold. Resetting only when the *player* changes glued
    the two turns together and produced a ladder that climbs to 17 and then
    falls to 0 — a descending ladder, which an ascending auction cannot produce.

    The damage is not cosmetic. An opponent model fitted on that ladder sees a
    bidding war that ended at zero.
    """
    rows = [
        {"auction_id": "a", "state": {"update_type": "first_call", "player_id": "p",
                                      "price": 0, "last_update": 1}},
        {"auction_id": "a", "state": {"update_type": "raise", "player_id": "p",
                                      "price": 17, "last_update": 2}},
        {"auction_id": "a", "state": {"update_type": "first_call", "player_id": "p",
                                      "price": 0, "last_update": 3}},
        {"auction_id": "a", "state": {"update_type": "close_auction", "player_id": "p",
                                      "price": 0, "last_update": 4}},
    ]
    (assignment,) = reconstruct(rows)
    assert assignment.price == 0
    assert [b.price for b in assignment.ladder] == [0], (
        "the annulled turn's bidding must not appear in the sale that followed"
    )


def test_no_recorded_ladder_ever_steps_downwards() -> None:
    """An ascending auction cannot produce one. This is the property the bug
    above violated, asserted across the whole recorded evening rather than on a
    fixture that happened not to contain the case."""
    if not EVENING.exists():
        pytest.skip("the recorded evening is not on this machine")
    descending = [
        a for a in reconstruct(_states(EVENING))
        if [b.price for b in a.ladder] != sorted(b.price for b in a.ladder)
    ]
    assert descending == [], (
        f"{len(descending)} ladder(s) step downwards; first: "
        f"{[b.price for b in descending[0].ladder] if descending else None}"
    )


def test_what_actually_stops_a_sale_being_counted_twice() -> None:
    """Not the `last_update` guard, which is what the docstring implied.

    Mutation-tested 2026-08-27: removing that guard changes nothing — the whole
    recorded evening reconstructs to the same 11,498 assignments and 70,152
    rungs. Two other rules do the work, and these are the ones worth pinning:

    * `sold` — first close per (auction, player) wins, so the node continuing to
      return a closed state cannot re-sell the player;
    * a rung is appended only on a *price change*, so a state observed twice at
      the same price adds nothing to the ladder.
    """
    close = {"update_type": "close_auction", "player_id": "p", "price": 9, "last_update": 4}
    rows = [
        {"auction_id": "a", "state": {"update_type": "first_call", "player_id": "p",
                                      "price": 0, "last_update": 1}},
        {"auction_id": "a", "state": {"update_type": "raise", "player_id": "p",
                                      "price": 9, "last_update": 2}},
        # The node keeps returning the closed state; a poller reads it repeatedly,
        # each time with a *different* last_update, so the guard cannot help here.
        {"auction_id": "a", "state": {**close, "last_update": 4}},
        {"auction_id": "a", "state": {**close, "last_update": 5}},
        {"auction_id": "a", "state": {**close, "last_update": 6}},
    ]
    (assignment,) = reconstruct(rows)
    assert assignment.price == 9
    assert [b.price for b in assignment.ladder] == [0, 9], (
        "a re-observation at the same price is the same offer, not a new rung"
    )
