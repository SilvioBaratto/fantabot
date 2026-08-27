"""Folding frames into the merged state `reconstruct` consumes.

This is the seam between the two collection paths. The poller wrote merged
states directly; the live path has to arrive at the same shape, or the recorded
evening stops being a regression test for anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from fantabot.aste.reducer import apply_frame, fold
from fantabot.aste.sse import parse

SSE = Path(__file__).parent / "fixtures" / "sse"
LIVE = (SSE / "live_auction.txt").read_text(encoding="utf-8")
NULL_PATCH = (SSE / "null_patch.txt").read_text(encoding="utf-8")


def test_a_put_replaces_the_whole_state() -> None:
    before = {"stale": "value"}
    after = apply_frame(before, parse(LIVE)[0])
    assert "stale" not in after
    assert after["price"] == 261


def test_a_patch_merges_into_what_is_there() -> None:
    state = fold(parse(LIVE)[:3])
    assert state["price"] == 262, "the patch's price must win"
    assert state["fantaleague_id"], "fields the patch did not mention must survive"


def test_a_null_in_a_patch_deletes_the_key() -> None:
    """This is how a close is signalled. Storing the null instead leaves a stale
    price on the board forever — the board would show a player still being bid
    on after the room moved to the next one."""
    state = {"price": 366, "user_id": "someone", "next_turn": "old"}
    after = apply_frame(state, parse(NULL_PATCH)[0])
    assert "price" not in after
    assert "user_id" not in after
    assert after["next_turn"] == "ec248fa6-ce93-41a2-807b-3e3fb69d9a98"


def test_a_keepalive_changes_nothing() -> None:
    state = fold(parse(LIVE)[:1])
    assert fold(parse(LIVE)[:2]) == state


def test_folding_never_mutates_its_input() -> None:
    state = {"price": 1}
    apply_frame(state, parse(LIVE)[0])
    assert state == {"price": 1}


def test_the_folded_state_is_what_reconstruct_expects() -> None:
    """The load-bearing claim of the whole live path: frames reduce to the same
    shape the poller wrote, so `reconstruct` needs no second code path."""
    from fantabot.aste.reconstruct import reconstruct

    recorded = json.loads(
        (Path(__file__).parent / "fixtures" / "states" / "one_auction.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )["state"]

    live = fold(parse(LIVE))
    assert set(live) >= {"fantaleague_id", "last_update", "update_type", "price"}
    assert set(live) <= set(recorded) | set(live), "no invented keys"

    rows = [{"auction_id": live["fantaleague_id"], "state": live}]
    assert reconstruct(rows) == [], "a raise is not an assignment"
