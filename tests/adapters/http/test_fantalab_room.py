"""The participant bid loop, driven by injected fakes. **No socket, no PATCH.**

The loop is a run with no end, so the test bounds it with ``keep_going`` and a scripted snapshot
sequence, then asserts the three things that matter: it bids its target up to the walk-away and
then stops, it never crosses budget, and it speaks every cycle with per-guard refusal counts.
The write is a fake — nothing leaves the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fantabot.adapters.http.fantalab.room import run_bid_loop
from fantabot.domain.asta.bid import Seat

SEAT = Seat(fantateam_id="seat2", user_id="me")
FL = "L"
FAR = 10_000_000  # now(): far past any last_bid_time, so the 500ms floor never trips


@dataclass
class _Outcome:
    sent: bool
    status: int | None


def _lot(price: int) -> dict[str, Any]:
    return {"player_id": "kean", "price": price, "user_id": "rival", "last_bid_time": 0}


def _run(snapshots: list[dict[str, Any] | None], *, walk_away: int, budget: int, sent: bool = True):
    reads = iter(snapshots)
    writes: list[dict[str, Any]] = []
    beats: list[str] = []

    def write(payload: dict[str, Any]) -> _Outcome:
        writes.append(payload)
        return _Outcome(sent=sent, status=200 if sent else None)

    report = run_bid_loop(
        seat=SEAT,
        fantaleague_id=FL,
        remaining_budget=budget,
        target_of=lambda snap: ("kean", walk_away),
        read=lambda: next(reads, None),
        write=write,
        now=lambda: FAR,
        sleep=lambda _s: None,
        keep_going=lambda cycle: cycle < len(snapshots),
        heartbeat=beats.append,
        poll_seconds=0.0,
    )
    return report, writes, beats


def test_bids_the_target_up_to_the_walk_away_then_stops() -> None:
    report, writes, beats = _run(
        [_lot(1), _lot(5), _lot(10), _lot(11)], walk_away=10, budget=100
    )
    assert [w["price"] for w in writes] == [2, 6]  # 11 and 12 would cross walk_away 10
    assert report.bids_sent == 2
    assert report.refused == {"walk_away": 2}
    assert len(beats) == 4  # a heartbeat every cycle


def test_never_crosses_budget() -> None:
    report, writes, _ = _run([_lot(1), _lot(5), _lot(10)], walk_away=100, budget=5)
    assert [w["price"] for w in writes] == [2]  # 6 and 11 are over the 5 budget
    assert report.refused == {"budget": 2}


def test_a_dry_run_write_counts_no_bid_sent() -> None:
    report, writes, beats = _run([_lot(1)], walk_away=100, budget=100, sent=False)
    assert len(writes) == 1  # decide_bid produced a payload...
    assert report.bids_sent == 0  # ...but the gated writer sent nothing
    assert "dry-run" in beats[0]


def test_waiting_and_not_a_target_cycles_place_no_bid() -> None:
    reads = iter([None, {"update_type": "reset"}])
    beats: list[str] = []
    report = run_bid_loop(
        seat=SEAT,
        fantaleague_id=FL,
        remaining_budget=100,
        target_of=lambda snap: None,
        read=lambda: next(reads, None),
        write=lambda payload: _Outcome(sent=True, status=200),
        now=lambda: FAR,
        sleep=lambda _s: None,
        keep_going=lambda cycle: cycle < 2,
        heartbeat=beats.append,
    )
    assert report.bids_sent == 0 and report.refused == {}
    assert "waiting" in beats[0]


class TestABudgetThatMoves:
    """`remaining_budget` was passed once and never updated.

    The loop believed it still held its opening credits after every purchase, so the
    `budget` guard — the one thing between a plan and an overdraft — was comparing each
    bid against a number that stopped being true at the first lot won. A live room is
    exactly where that goes wrong: the ledger the caller reads has already moved.
    """

    def test_a_callable_budget_is_resolved_every_cycle(self) -> None:
        purse = [100]
        reads = iter([_lot(30), _lot(30), _lot(30)])
        writes: list[dict[str, Any]] = []

        def write(payload: dict[str, Any]) -> _Outcome:
            writes.append(payload)
            purse[0] -= 40  # as if the lot cleared and the ledger moved
            return _Outcome(sent=True, status=200)

        report = run_bid_loop(
            seat=SEAT,
            fantaleague_id=FL,
            remaining_budget=lambda: purse[0],
            target_of=lambda snap: ("kean", 100),
            read=lambda: next(reads, None),
            write=write,
            now=lambda: FAR,
            sleep=lambda _s: None,
            keep_going=lambda cycle: cycle < 3,
            heartbeat=lambda _line: None,
        )

        # 100 -> bid 31 -> 60 -> bid 31 -> 20, and 31 is now over budget. With the old
        # fixed int the third bid would still have been measured against the opening 100.
        assert [w["price"] for w in writes] == [31, 31]
        assert report.refused == {"budget": 1}
        assert purse[0] == 20

    def test_a_plain_int_still_works(self) -> None:
        """Every existing caller passes an int, and the replay paths have no ledger."""
        report, writes, _ = _run([_lot(1), _lot(5)], walk_away=100, budget=100)

        assert len(writes) == 2
        assert report.refused == {}


class TestCtrlCReports:
    """The summary at the end of `asta bid` was unreachable.

    `keep_going` is `lambda _cycle: True` in production, so the loop never returns and the
    caller's report line never printed. The one thing an operator wants on the way out —
    how many cycles ran, how many bids went, and which guard refused the rest — was
    computed every cycle and then thrown away on Ctrl-C.
    """

    def test_the_report_survives_a_keyboard_interrupt(self) -> None:
        beats: list[str] = []

        def read() -> dict[str, Any]:
            if len(beats) >= 2:
                raise KeyboardInterrupt
            return _lot(1)

        report = run_bid_loop(
            seat=SEAT,
            fantaleague_id=FL,
            remaining_budget=100,
            target_of=lambda snap: ("kean", 0),  # refused on walk_away, so no writes
            read=read,
            write=lambda payload: _Outcome(sent=True, status=200),
            now=lambda: FAR,
            sleep=lambda _s: None,
            keep_going=lambda _cycle: True,
            heartbeat=beats.append,
        )

        # Three: the interrupted poll was entered, and the heartbeat says which one it was.
        assert report.cycles == 3
        assert beats[-1].endswith("interrupted")
        assert report.refused == {"walk_away": 2}
        assert report.bids_sent == 0
