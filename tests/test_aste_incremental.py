"""The resumable fold agrees with the whole-file one, across every split.

The property that matters is not "it produces sales" — it is that folding the same
records in *any* number of passes produces exactly what one pass produces, ladders
included. `compare.equivalent` is the oracle, because a price-only check cannot see
the failure this whole exercise exists to prevent: a window starting mid-turn rebuilds
a ladder from nothing, the upsert is `DO UPDATE`, and the short ladder silently
overwrites the complete one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fantabot.aste import incremental as I
from fantabot.aste.compare import equivalent
from fantabot.aste.models import Bid
from fantabot.aste.reconstruct import reconstruct

FIXTURE = Path(__file__).parent / "fixtures" / "states" / "one_auction.jsonl"


def _records() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _drain(emitted: list[Any]) -> list[Any]:
    """What the database ends up holding: last close per key wins.

    `advance` emits every close it sees, and `upsert_assignments` is
    `ON CONFLICT DO UPDATE` on `(asta_id, player_uuid)`, so this mirrors the write.
    """
    final: dict[tuple[str, str], Any] = {}
    for a in emitted:
        final[(a.auction_id, a.player_id)] = a
    return list(final.values())


def _fold_in(records: list[dict[str, Any]], chunks: int) -> list[Any]:
    state = I.empty()
    emitted: list[Any] = []
    step = max(1, len(records) // chunks + 1)
    for start in range(0, len(records), step):
        state, closed = I.advance(state, records[start : start + step])
        emitted.extend(closed)
    return _drain(emitted)


@pytest.mark.parametrize("chunks", [1, 2, 3, 5, 7, 11, 50, 1000])
def test_any_split_reproduces_the_whole_file_fold(chunks: int) -> None:
    """Pass boundaries are arbitrary — the follower's window is bytes, not turns.

    So the fold must be indifferent to where they fall, including a boundary between
    a raise and the close that ends the same ladder.
    """
    records = _records()
    verdict = equivalent(reconstruct(records), _fold_in(records, chunks))
    assert verdict.ok, f"{chunks} passes: {verdict.reason}"


def test_one_record_at_a_time_is_still_identical() -> None:
    """The worst case for a carried ladder, and the cheapest to get wrong."""
    records = _records()
    verdict = equivalent(reconstruct(records), _fold_in(records, len(records)))
    assert verdict.ok, verdict.reason


def test_state_survives_a_round_trip_through_json() -> None:
    """A checkpoint is only useful if what comes back folds the same way."""
    records = _records()
    half = len(records) // 2

    state, first = I.advance(I.empty(), records[:half])
    restored = I.from_json(json.loads(json.dumps(I.to_json(state))))
    assert restored is not None

    _, direct = I.advance(state, records[half:])
    _, resumed = I.advance(restored, records[half:])
    assert _drain(first + direct) == _drain(first + resumed)

    verdict = equivalent(reconstruct(records), _drain(first + resumed))
    assert verdict.ok, verdict.reason


def test_the_carried_ladder_is_what_makes_it_work() -> None:
    """Split mid-turn and the second half alone cannot know the earlier rungs.

    This is the failure the whole-file rebuild existed to avoid, reproduced: fold the
    tail with a *fresh* state and the ladder comes out short. That the resumed state
    does not is the entire claim of this module.
    """
    records = _records()
    half = len(records) // 2
    state, first = I.advance(I.empty(), records[:half])

    _, cold = I.advance(I.empty(), records[half:])
    _, warm = I.advance(state, records[half:])

    whole = reconstruct(records)
    assert equivalent(whole, _drain(first + warm)).ok
    assert not equivalent(whole, _drain(cold)).ok, (
        "a fold with no carried state should have produced something different — "
        "if it did not, this fixture never splits a turn and proves nothing"
    )


class TestStateFileIsRefusedRatherThanGuessedAt:
    """`from_json` returns None on anything it does not fully understand.

    None means "re-fold from the beginning": correct, and merely slow. Adapting a
    layout we no longer understand would put a wrong ladder into an append-only
    archive, which is the one cost this package will not pay.
    """

    def test_a_newer_version_is_refused(self) -> None:
        blob = I.to_json(I.empty())
        blob["version"] = I.STATE_VERSION + 1
        assert I.from_json(blob) is None

    def test_a_missing_version_is_refused(self) -> None:
        blob = I.to_json(I.empty())
        del blob["version"]
        assert I.from_json(blob) is None

    def test_a_truncated_file_is_refused(self) -> None:
        blob = I.to_json(I.empty())
        del blob["ladders"]
        assert I.from_json(blob) is None

    def test_a_malformed_ladder_is_refused(self) -> None:
        state = I.FoldState(ladders={"a": (Bid(price=1, team_id="t", at_ms=1),)})
        blob = I.to_json(state)
        blob["ladders"]["a"] = [["not-a-price", "t", 1]]
        assert I.from_json(blob) is None

    def test_not_a_mapping_is_refused(self) -> None:
        assert I.from_json([1, 2, 3]) is None
        assert I.from_json(None) is None


def test_an_auction_is_known_only_once_it_has_been_folded() -> None:
    """`known` is how the caller tells a resumed auction from an adopted one.

    An auction that opened after the follower started has streamed past while its
    events were being dropped as unknown; its history has to be rebuilt, not resumed.
    """
    records = _records()
    state, _ = I.advance(I.empty(), records)
    auctions = {r["auction_id"] for r in records if isinstance(r.get("auction_id"), str)}

    assert auctions
    assert all(state.sees(a) for a in auctions)
    assert not state.sees("an-auction-that-opened-later")
    assert not I.empty().sees(next(iter(auctions)))
