"""Comparing a polled capture with a streamed one.

T16's criterion is deliberately **strict superset**, not equality. Both
collectors watch the same rooms, so they must agree on every sale; the streamed
one must additionally hold rungs the poller could not see, because a poll reads
a merged snapshot and two raises inside one interval collapse into one.

Equality is therefore a failure, not a pass. It would mean the reducer is
producing exactly what polling produced — which is the outcome this phase exists
to improve on.
"""

from __future__ import annotations

import pytest

from fantabot.domain.harvest.compare import Verdict, compare
from fantabot.domain.harvest.models import Assignment, Bid


def _assignment(player: str, price: int, rungs: list[int]) -> Assignment:
    return Assignment(
        auction_id="a-1",
        player_id=player,
        price=price,
        buyer_team_id="t",
        closed_at_ms=1,
        ladder=tuple(Bid(price=p, team_id="t", at_ms=p) for p in rungs),
    )


def test_a_strict_superset_passes() -> None:
    polled = [_assignment("p1", 10, [0, 10])]
    streamed = [_assignment("p1", 10, [0, 3, 7, 10])]
    verdict = compare(polled, streamed)
    assert verdict.ok
    assert verdict.extra_rungs == 2


def test_identical_ladders_fail_because_that_proves_nothing_was_gained() -> None:
    """If streaming sees exactly what polling saw, the subscription bought
    nothing and the reducer should be suspected before it is trusted."""
    same = [_assignment("p1", 10, [0, 10])]
    verdict = compare(same, list(same))
    assert not verdict.ok
    assert "no rung" in verdict.reason


def test_a_missing_sale_fails() -> None:
    """Streaming must not lose an assignment polling caught. That direction is a
    regression, and no number of extra rungs excuses it."""
    polled = [_assignment("p1", 10, [0, 10]), _assignment("p2", 4, [0, 4])]
    streamed = [_assignment("p1", 10, [0, 3, 10])]
    verdict = compare(polled, streamed)
    assert not verdict.ok
    assert "p2" in verdict.reason


def test_a_disagreement_on_price_fails() -> None:
    """Both watched the same room. A different clearing price means one of them
    is reading the node wrong, and the numbers cannot be pooled until it is known
    which."""
    polled = [_assignment("p1", 10, [0, 10])]
    streamed = [_assignment("p1", 99, [0, 50, 99])]
    verdict = compare(polled, streamed)
    assert not verdict.ok
    assert "price" in verdict.reason


def test_sales_only_streaming_saw_are_reported_not_faulted() -> None:
    """Polling starts and stops on its own schedule and misses openings. Extra
    sales are expected, and counted rather than treated as a mismatch."""
    polled = [_assignment("p1", 10, [0, 10])]
    streamed = [_assignment("p1", 10, [0, 5, 10]), _assignment("p2", 7, [0, 7])]
    verdict = compare(polled, streamed)
    assert verdict.ok
    assert verdict.only_streamed == 1


@pytest.mark.parametrize("polled", [[], [_assignment("p", 1, [1])]])
def test_an_empty_streamed_capture_never_passes(polled: list[Assignment]) -> None:
    """Nothing collected is not a superset of anything, including nothing. A run
    that captured no sales has not demonstrated the property."""
    assert not compare(polled, []).ok


def test_the_verdict_reads_as_a_sentence() -> None:
    verdict = compare([_assignment("p1", 10, [0, 10])], [_assignment("p1", 10, [0, 5, 10])])
    assert "superset" in verdict.summary().lower()
    assert isinstance(verdict, Verdict)


def _timed(player: str, price: int, rungs: list[int], closed_ms: int) -> Assignment:
    return Assignment(
        auction_id="a-1", player_id=player, price=price, buyer_team_id="t",
        closed_at_ms=closed_ms,
        ladder=tuple(Bid(price=p, team_id="t", at_ms=p) for p in rungs),
    )


def test_sales_outside_the_shared_window_are_not_counted_against_streaming() -> None:
    """Two processes cannot start on the same millisecond. In the first shadow
    run the poller led by ten seconds and caught three closes before the streamer
    had connected — reported, correctly, as three lost sales.

    Comparing outside the overlap compares two different observation periods.
    The superset requirement stays strict *within* the window; it just stops
    asking streaming to account for a time it was not watching.
    """
    polled = [_timed("early", 1, [0, 1], closed_ms=100), _timed("shared", 5, [0, 5], 500)]
    streamed = [_timed("shared", 5, [0, 2, 5], 500)]
    verdict = compare(polled, streamed, window=(400, 900))
    assert verdict.ok, verdict.reason
    assert verdict.outside_window == 1


def test_the_window_is_derived_from_the_captures_when_not_given() -> None:
    """The caller should not have to compute it: the overlap is a property of the
    two files, and asking for it by hand invites getting it wrong quietly."""
    polled = [_timed("early", 1, [0, 1], 100), _timed("shared", 5, [0, 5], 500)]
    streamed = [_timed("shared", 5, [0, 2, 5], 500)]
    assert compare(polled, streamed).ok


def test_a_sale_lost_inside_the_window_still_fails() -> None:
    """The window narrows what is compared; it must not soften the verdict."""
    polled = [_timed("a", 1, [0, 1], 500), _timed("b", 2, [0, 2], 600)]
    streamed = [_timed("a", 1, [0, 0, 1], 500)]
    verdict = compare(polled, streamed, window=(400, 900))
    assert not verdict.ok
    assert "b" in verdict.reason


def test_the_window_comes_from_when_each_side_was_watching() -> None:
    """Derived from *close* times, the window was wrong for a reason worth
    naming: a close is a server event, and either collector may observe one that
    happened before it connected — the node keeps returning the closed state
    until the next call. The poller's first read caught two such closes, both
    timestamped inside the derived window, so it excused nothing.

    What bounds a fair comparison is when each side was *watching*, which lives
    in the capture's `seen_at` and not in the assignment at all.
    """
    from fantabot.domain.harvest.compare import observation_window

    polled = [{"seen_at": "2026-08-27T18:38:26+00:00"},
              {"seen_at": "2026-08-27T18:45:00+00:00"}]
    streamed = [{"seen_at": "2026-08-27T18:38:36+00:00"},
                {"seen_at": "2026-08-27T18:44:00+00:00"}]
    start, end = observation_window(polled, streamed)
    assert start == int(
        __import__("datetime").datetime.fromisoformat("2026-08-27T18:38:36+00:00").timestamp() * 1000
    ), "the window starts when the later of the two connected"
    assert end < start + 6 * 60 * 1000, "and ends when the earlier of the two stopped"


def test_an_observation_window_needs_both_sides() -> None:
    from fantabot.domain.harvest.compare import observation_window

    assert observation_window([], [{"seen_at": "2026-08-27T18:00:00+00:00"}]) is None
