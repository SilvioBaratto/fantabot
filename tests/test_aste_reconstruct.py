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
