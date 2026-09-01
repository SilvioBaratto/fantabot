"""A failed poll is counted *and seen*.

The loop already survives a bad cycle and counts it. What it could not do is show it to the
one caller with no heartbeat: `asta room` discards heartbeat lines — the screen is the frame —
and surfaced errors by sniffing that line's text for "Error" / "error" / "timed out".

That filter missed `ReadTimeout`, `ConnectTimeout` and `PoolTimeout`, which on a flaky link
are the three most likely failures there are. The result was the exact state the counting
exists to prevent: a room that looks quiet while the network is gone, walk-aways frozen on
screen, and nothing bidding.

So the channel is structured now. `on_error(exc, consecutive)` receives the exception, not a
rendering of it, and `asta room` paints it into the `Live` — a `console.print` from inside a
`Live(screen=True)` lands in an alternate buffer the next refresh overwrites.
"""

from __future__ import annotations

from typing import Any

import httpx

from fantabot.adapters.http.fantalab.room import run_bid_loop
from fantabot.domain.asta.bid import Seat

SEAT = Seat(fantateam_id="seat2", user_id="me")
FAR = 10_000_000


def _drive(reads: list[Any], *, cycles: int, on_error: Any = None, heartbeat: Any = None):
    seen = iter(reads)

    def read() -> Any:
        item = next(seen)
        if isinstance(item, Exception):
            raise item
        return item

    return run_bid_loop(
        seat=SEAT,
        fantaleague_id="L",
        remaining_budget=500,
        max_cap=None,
        target_of=lambda _snapshot: None,
        read=read,
        write=lambda _payload: None,
        now=lambda: FAR,
        sleep=lambda _seconds: None,
        keep_going=lambda cycle: cycle < cycles,
        heartbeat=heartbeat if heartbeat is not None else (lambda _line: None),
        poll_seconds=0,
        on_error=on_error,
    )


def _lot() -> dict[str, Any]:
    return {"player_id": "kean", "price": 1, "user_id": "rival", "last_bid_time": 0}


class TestTheTimeoutsThatUsedToBeSilent:
    def test_every_httpx_timeout_class_reaches_on_error(self) -> None:
        """The regression, named. None of these three contains "Error", "error" or
        "timed out" in its class name, which is what the old filter matched on."""
        for exc in (
            httpx.ReadTimeout("read"),
            httpx.ConnectTimeout("connect"),
            httpx.PoolTimeout("pool"),
        ):
            seen: list[Exception] = []
            _drive([exc], cycles=1, on_error=lambda e, _n, sink=seen: sink.append(e))
            assert seen == [exc], f"{type(exc).__name__} was not reported"

    def test_the_exception_arrives_as_an_object_not_a_string(self) -> None:
        """Classifying a failure by the text of its rendering is what failed. The callback
        gets the exception, so a caller can branch on its type."""
        seen: list[Exception] = []
        _drive([httpx.ReadTimeout("blip")], cycles=1, on_error=lambda e, _n: seen.append(e))

        assert isinstance(seen[0], httpx.TimeoutException)


class TestTheStreak:
    def test_consecutive_failures_are_counted(self) -> None:
        """1 is a blip, 200 is a wrong shard or a dead link. Both are a red screen; only the
        count tells the operator which evening they are having."""
        seen: list[int] = []
        _drive([httpx.ConnectError("x")] * 3, cycles=3, on_error=lambda _e, n: seen.append(n))

        assert seen == [1, 2, 3]

    def test_one_good_poll_resets_it(self) -> None:
        seen: list[int] = []
        _drive(
            [httpx.ConnectError("x"), httpx.ConnectError("x"), _lot(), httpx.ConnectError("x")],
            cycles=4,
            on_error=lambda _e, n: seen.append(n),
        )
        assert seen == [1, 2, 1]


class TestTheDefaultCallerIsUnchanged:
    def test_without_on_error_the_heartbeat_still_speaks(self) -> None:
        """`asta bid` prints its heartbeat to a real console and needs no screen."""
        beats: list[str] = []
        _drive([httpx.ReadTimeout("blip")], cycles=1, heartbeat=beats.append)

        assert len(beats) == 1
        assert "ReadTimeout" in beats[0]
        assert "blip" in beats[0]
        assert "1 in a row" in beats[0]

    def test_on_error_replaces_the_heartbeat_rather_than_doubling_it(self) -> None:
        beats: list[str] = []
        _drive(
            [httpx.ReadTimeout("blip")],
            cycles=1,
            heartbeat=beats.append,
            on_error=lambda _e, _n: None,
        )
        assert beats == []

    def test_the_report_still_counts_by_class(self) -> None:
        report = _drive(
            [httpx.ReadTimeout("a"), httpx.ConnectError("b"), httpx.ReadTimeout("c")],
            cycles=3,
            on_error=lambda _e, _n: None,
        )
        assert report.errors == {"ReadTimeout": 2, "ConnectError": 1}
        # A link failure is not a bidding decision and must not be filed as one.
        assert report.refused == {}
