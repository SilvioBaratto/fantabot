"""Folding frames into the merged state `reconstruct` consumes.

This is the seam between the two collection paths. The poller wrote merged
states directly; the live path has to arrive at the same shape, or the recorded
evening stops being a regression test for anything.
"""

from __future__ import annotations

import json

from _paths import ONE_AUCTION, SSE_FIXTURES

from fantabot.domain.harvest.reducer import apply_frame, fold
from fantabot.domain.harvest.sse import parse

SSE = SSE_FIXTURES
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
    from fantabot.domain.harvest.reconstruct import reconstruct

    recorded = json.loads(
        ONE_AUCTION
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )["state"]

    live = fold(parse(LIVE))
    assert set(live) >= {"fantaleague_id", "last_update", "update_type", "price"}
    assert set(live) <= set(recorded) | set(live), "no invented keys"

    rows = [{"auction_id": live["fantaleague_id"], "state": live}]
    assert reconstruct(rows) == [], "a raise is not an assignment"


NESTED_NULL = 'event: put\ndata: {"path":"/price","data":null}\n\n'
NESTED_PUT = 'event: put\ndata: {"path":"/price","data":42}\n\n'


def test_a_nested_put_does_not_replace_the_whole_state() -> None:
    """`Frame.path` was parsed and then consulted by nothing, so a frame aimed at
    a child key was applied at the root: a nested `put` wiped the auction.

    `tasks/archive/aste-streaming-spec.md`'s own Code Style snippet refuses a non-root
    path — that guard was specified and never implemented.
    """
    state = fold(parse(LIVE)[:1])
    assert state["price"] == 261
    after = apply_frame(state, parse(NESTED_PUT)[0])
    assert after == state, "an unhandled path must leave the node as it was"


def test_a_nested_null_put_is_not_the_room_closing() -> None:
    """The dangerous half. `watch_auction` reads a `put` with null data as the
    auction ending, and did so without checking the path — so a child deletion
    dropped the auction for the rest of the evening while the report called it a
    normal ending."""
    from fantabot.adapters.http.harvest.stream import is_auction_gone

    assert is_auction_gone(parse('event: put\ndata: {"path":"/","data":null}\n\n')[0])
    assert not is_auction_gone(parse(NESTED_NULL)[0])


def test_an_unhandled_path_is_counted_rather_than_silently_dropped() -> None:
    """Refusing is right; refusing in silence is the failure this phase keeps
    finding in itself."""
    from fantabot.domain.harvest.reducer import unsupported_paths

    counter = unsupported_paths()
    apply_frame({}, parse(NESTED_PUT)[0], seen=counter)
    assert counter["/price"] == 1
