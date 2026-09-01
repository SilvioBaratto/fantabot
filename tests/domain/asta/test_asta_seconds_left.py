"""How long the lot on the block has, from the snapshot alone.

`remaining = (is_first ? counter_time_first : counter_time) - (now - last_bid_time)`
(`docs/fantalab/01-auction-engine.md:263`). The first call gets a longer clock — 20 s against
10 s by default — because a called player has to be noticed before he can be bid on.

Pure, and it takes `now_ms` as a parameter for the same reason `sentiment.as_of` does: a
module that reads the clock has tests that are a coin flip.
"""

from __future__ import annotations

from fantabot.domain.asta.live import seconds_left

TIMERS = {"counter_time": 10, "counter_time_first": 20}


def _lot(last_bid_time: int | None, **extra: object) -> dict[str, object]:
    return {"player_id": "kean", "price": 1, "last_bid_time": last_bid_time, **extra}


class TestTheCountdown:
    def test_a_fresh_raise_has_the_whole_raise_timer(self) -> None:
        assert seconds_left(_lot(1_000), now_ms=1_000, **TIMERS) == 10.0

    def test_it_counts_down_in_real_time(self) -> None:
        assert seconds_left(_lot(1_000), now_ms=4_000, **TIMERS) == 7.0

    def test_an_expired_timer_is_zero_rather_than_negative(self) -> None:
        """A negative countdown would render as a number growing backwards, and the pane
        turns red on "under two seconds" — which every negative satisfies for ever."""
        assert seconds_left(_lot(1_000), now_ms=99_000, **TIMERS) == 0.0

    def test_the_first_call_gets_the_longer_clock(self) -> None:
        """20 s, not 10: a called player has to be noticed before he can be bid on."""
        first = _lot(1_000, price=0, user_id=None)

        assert seconds_left(first, now_ms=1_000, **TIMERS) == 20.0


class TestWhatItRefusesToGuess:
    def test_no_last_bid_time_is_unknown_rather_than_zero(self) -> None:
        """`None` renders as "--". Zero renders as "expired", which is a different claim."""
        assert seconds_left(_lot(None), now_ms=5_000, **TIMERS) is None

    def test_a_missing_snapshot_is_unknown(self) -> None:
        assert seconds_left(None, now_ms=5_000, **TIMERS) is None

    def test_a_boolean_last_bid_time_is_not_an_int(self) -> None:
        """`True` is an `int` in Python. The rest of this package coerces the same way."""
        assert seconds_left(_lot(True), now_ms=5_000, **TIMERS) is None

    def test_absent_timers_fall_back_to_the_platform_defaults(self) -> None:
        """A room that does not say uses FantaLab's own 20/10 (`docs/fantalab/01:22`)."""
        assert seconds_left(_lot(1_000), now_ms=1_000, counter_time=None,
                            counter_time_first=None) == 10.0
