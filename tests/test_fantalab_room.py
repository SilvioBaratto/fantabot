"""The participant bid loop, driven by injected fakes. **No socket, no PATCH.**

The loop is a run with no end, so the test bounds it with ``keep_going`` and a scripted snapshot
sequence, then asserts the three things that matter: it bids its target up to the walk-away and
then stops, it never crosses budget, and it speaks every cycle with per-guard refusal counts.
The write is a fake — nothing leaves the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fantabot.asta_engine.bid import Seat
from fantabot.fantalab.room import run_bid_loop

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
