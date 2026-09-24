"""The modificatore difesa's risk: P(four defenders vote), and the cross-role swap. Pure."""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.defence import (
    defenders_in,
    p_defenders_voting,
    swapped_module,
)

MODS = ("343", "352", "433", "442", "451", "532", "541")


def test_four_certain_defenders_always_vote() -> None:
    assert p_defenders_voting([1.0] * 4, []) == pytest.approx(1.0)


def test_fewer_than_four_starting_defenders_never_earn_it() -> None:
    assert p_defenders_voting([1.0] * 3, [1.0, 1.0]) == 0.0


def test_one_doubtful_defender_with_no_cover_is_his_own_probability() -> None:
    assert p_defenders_voting([1.0, 1.0, 1.0, 0.3], []) == pytest.approx(0.3)


def test_a_bench_defender_covers_an_absent_starter() -> None:
    # the doubtful starter misses 70% of the time; a sure bench defender then comes on.
    assert p_defenders_voting([1.0, 1.0, 1.0, 0.3], [1.0]) == pytest.approx(1.0)
    # an unlikely bench defender covers only part of it: 0.3 + 0.7 * 0.5
    assert p_defenders_voting([1.0, 1.0, 1.0, 0.3], [0.5]) == pytest.approx(0.65)


def test_one_bench_defender_covers_only_one_absence() -> None:
    # two starters out, one sure reserve: three voting, not four.
    assert p_defenders_voting([1.0, 1.0, 0.0, 0.0], [1.0]) == pytest.approx(0.0)


def test_a_back_five_tolerates_one_absence() -> None:
    assert p_defenders_voting([1.0, 1.0, 1.0, 1.0, 0.0], []) == pytest.approx(1.0)


def test_the_swap_turns_a_back_four_into_a_back_three() -> None:
    assert swapped_module("442", from_role="D", to_role="C", allowed=MODS) == "352"
    assert swapped_module("433", from_role="D", to_role="C", allowed=MODS) == "343"
    assert swapped_module("541", from_role="D", to_role="C", allowed=MODS) == "451"


def test_a_swap_into_a_module_the_lega_does_not_allow_is_refused() -> None:
    assert swapped_module("451", from_role="D", to_role="C", allowed=MODS) is None  # 361


def test_defenders_in() -> None:
    assert [defenders_in(m) for m in ("343", "442", "532")] == [3, 4, 5]
