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
from typing import Any, ClassVar

import pytest
from _paths import ONE_AUCTION

from fantabot.aste import incremental as I
from fantabot.aste.compare import equivalent
from fantabot.aste.models import Bid
from fantabot.aste.reconstruct import reconstruct

FIXTURE = ONE_AUCTION


def _records() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


#: The production drain. Tested directly below rather than reimplemented here — a
#: helper that duplicated the rule would pass while the shipped one was wrong.
_drain = I.drain


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


class TestOneWindowCanCarryTheSameSaleTwice:
    """`drain` is not tidiness — without it the write raises and the loop wedges.

    The node keeps returning a closed state until the next call begins, so one window
    routinely observes the same sale more than once. `reconstruct` absorbed that in its
    `sold` map before returning; `advance` deliberately does not, so the caller must.

    **`compare.equivalent` cannot catch this.** Its `_by_key` is a dict comprehension,
    so a doubled emission collapses on the way in and compares equal. These tests count
    rows.
    """

    @staticmethod
    def _reemitted_close() -> list[dict[str, Any]]:
        def state(**over: Any) -> dict[str, Any]:
            base = {
                "update_type": "raise",
                "player_id": "p",
                "price": 5,
                "fantateam_id": "t1",
            }
            return {"auction_id": "a", "state": base | over}

        return [
            state(update_type="first_call", price=0, fantateam_id=None, last_update=1),
            state(last_update=2),
            state(update_type="close_auction", last_update=3),
            # The held state, read again on the next poll.
            state(update_type="close_auction", last_update=4),
        ]

    def test_advance_emits_the_close_twice(self) -> None:
        _, closed = I.advance(I.empty(), self._reemitted_close())
        assert len(closed) == 2, "the fixture no longer re-emits; this test proves nothing"
        assert len({(a.auction_id, a.player_id) for a in closed}) == 1

    def test_drain_reduces_it_to_one_row_keeping_the_later(self) -> None:
        _, closed = I.advance(I.empty(), self._reemitted_close())
        drained = I.drain(closed)

        assert len(drained) == 1
        assert drained[0].closed_at_ms == 4, (
            "first-wins was tried and cost 1,814 credits and 175 buyers — see reconstruct"
        )

    def test_the_oracle_is_blind_to_it_which_is_why_these_count(self) -> None:
        """Pinning the limitation, so nobody later assumes `equivalent` covers it."""
        records = self._reemitted_close()
        _, closed = I.advance(I.empty(), records)

        assert equivalent(reconstruct(records), closed).ok
        assert len(closed) != len(reconstruct(records))


class TestAFailedWriteMustNotCorruptTheLadder:
    """The retry folds the same window again; the state must not have moved.

    `harvest load` writes, then advances the byte offset. A failed write leaves the
    offset where it was, so the window is re-read — and folding it onto a state that
    already holds it appends the rungs a second time.
    """

    #: Opens mid-ladder, deliberately. A window opening on `first_call` self-heals,
    #: because the reset clears the ladder, and a fixture split there passes vacuously.
    WINDOW: ClassVar[list[dict[str, Any]]] = [
        {"auction_id": "a", "state": {"update_type": "raise", "player_id": "p",
                                      "price": 6, "fantateam_id": "t1", "last_update": 6}},
        {"auction_id": "a", "state": {"update_type": "raise", "player_id": "p",
                                      "price": 7, "fantateam_id": "t2", "last_update": 7}},
    ]

    @staticmethod
    def _mid_turn() -> I.FoldState:
        state, _ = I.advance(I.empty(), [
            {"auction_id": "a", "state": {"update_type": "first_call", "player_id": "p",
                                          "price": 0, "fantateam_id": None, "last_update": 1}},
            {"auction_id": "a", "state": {"update_type": "raise", "player_id": "p",
                                          "price": 5, "fantateam_id": "t1", "last_update": 5}},
        ])
        return state

    def test_keeping_the_old_state_makes_the_retry_a_no_op(self) -> None:
        """What the caller must do: bind the new state only after the write commits."""
        before = self._mid_turn()

        attempt, _ = I.advance(before, self.WINDOW)      # write fails; `before` is kept
        retry, _ = I.advance(before, self.WINDOW)        # same window, same input state

        assert [b.price for b in retry.ladders["a"]] == [b.price for b in attempt.ladders["a"]]
        assert [b.price for b in retry.ladders["a"]] == [0, 5, 6, 7]

    def test_binding_the_new_state_before_the_write_corrupts_the_ladder(self) -> None:
        """The bug this ordering exists to prevent.

        The per-turn `seen_this_turn` guard absorbs a *straight* replay of the same
        window — the stamps are already there, so the rungs are not re-appended, and
        an earlier draft of this test asserted a descending ladder that no longer
        occurs. It is not idempotent in general: replay across a turn boundary
        re-resets the ladder and rungs are lost, measured at 21 rungs against 29 over
        the real fixture. So the ordering discipline remains the defence and the guard
        is a second line, not a replacement.
        """
        records = _records()

        def fold(step: int, twice: bool) -> list[tuple[str, tuple[int, ...]]]:
            """What the database ends up holding, ladders included.

            The comparison is on *emitted* assignments, not on `state.ladders`: the
            state holds only the turn currently on the block, so a ladder corrupted
            and then closed leaves no trace there. What is written is what matters.
            """
            state = I.empty()
            emitted: list[Any] = []
            for start in range(0, len(records), step):
                window = records[start : start + step]
                state, closed = I.advance(state, window)
                emitted.extend(closed)
                if twice:  # the retry, with the state already bound
                    state, closed = I.advance(state, window)
                    emitted.extend(closed)
            return sorted(
                (a.player_id, tuple(b.price for b in a.ladder)) for a in I.drain(emitted)
            )

        # Whether a replay is harmless depends on where the window boundary falls: a
        # straight re-fold inside one turn is absorbed by `seen_this_turn`, while one
        # spanning a turn boundary re-resets the ladder and drops rungs. At least one
        # split must corrupt, or the ordering rule would have nothing to protect.
        damaged = [
            step
            for step in (7, 13, 40, 97, len(records) // 7 or 1)
            if fold(step, twice=True) != fold(step, twice=False)
        ]
        assert damaged, (
            "no window size was corrupted by a replay — if the fold really is "
            "idempotent across turn boundaries, this test and the ordering rule in "
            "FoldState's docstring should both be revisited"
        )
