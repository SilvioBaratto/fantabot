"""The per-query loop bound. Pure and synchronous — just the constant.

`MAX_TURNS` is the backstop on how many turns one player's query may take. Each
turn re-sends every page already fetched as input, so a lower cap directly bounds
the run's dominant cost. 8 aligns with the prompt's own ceiling (≈2 searches + ≈4
source reads + reasoning + answer); it is a backstop, not a target.
"""

from __future__ import annotations

from fantabot.news import pipeline


def test_max_turns_is_bounded_to_eight() -> None:
    assert pipeline.MAX_TURNS == 8


def test_max_turns_is_lower_than_the_old_backstop() -> None:
    # It was 12; the point of Task 3 is that it came down.
    assert pipeline.MAX_TURNS < 12
