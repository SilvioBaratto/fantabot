"""Each player's ranking signal: the sourced `indexCompare`, and the projection's
sub-aware term. Pure."""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.models import RosterPlayer
from fantabot.domain.lineup.value import replacement_level, score, sub_aware

ROSTER = [
    RosterPlayer(id=6482, roles=frozenset({"POR"}), fvmma=6.0),
    RosterPlayer(id=4179, roles=frozenset({"W", "A"}), fvmma=40.0),
]


def test_score_is_the_per_player_value() -> None:
    assert score(ROSTER) == {6482: 6.0, 4179: 40.0}


def test_an_empty_roster_scores_to_an_empty_map() -> None:
    assert score([]) == {}


# --- the sub-aware term (T18, SPEC A17(3) with one replacement level) -------


def test_the_replacement_level_is_the_next_man_up() -> None:
    """With 3 starters, the 4th-best expected score is what the auto-sub gets."""
    expected = {1: 7.0, 2: 6.0, 3: 5.0, 4: 4.5, 5: 1.0}

    assert replacement_level(expected, starters=3) == 4.5


def test_a_roster_shorter_than_the_xi_falls_back_to_its_worst() -> None:
    assert replacement_level({1: 7.0, 2: 6.0}, starters=3) == 6.0


def test_an_empty_roster_has_no_replacement() -> None:
    assert replacement_level({}, starters=3) == 0.0


def test_a_certain_starter_is_worth_his_own_projection() -> None:
    mu = {1: 7.0, 2: 6.0, 3: 5.0}
    p = {1: 1.0, 2: 1.0, 3: 1.0}

    assert sub_aware(mu, p, starters=2) == {1: 7.0, 2: 6.0, 3: 5.0}


def test_a_player_who_never_plays_is_worth_the_replacement() -> None:
    """His own μ never arrives; the man who comes on for him is the 3rd best, at 4.0."""
    mu = {1: 100.0, 2: 6.0, 3: 5.0, 4: 4.0}
    p = {1: 0.0, 2: 1.0, 3: 1.0, 4: 1.0}

    assert sub_aware(mu, p, starters=2)[1] == 4.0


def test_a_fragile_star_is_priced_against_the_bench_not_against_zero() -> None:
    """The disagreement with p·μ, and its direction. p·μ prices the star's absence at 0 and
    ranks the safe man first (5.0 against 4.5); with a 4.0 man coming on for him the star
    is worth 6.5, and he is the one who starts."""
    mu = {"star": 9.0, "safe": 5.0, "bench": 4.0}
    p = {"star": 0.5, "safe": 1.0, "bench": 1.0}

    values = sub_aware(mu, p, starters=2)

    assert values["star"] == pytest.approx(0.5 * 9.0 + 0.5 * 4.0)
    assert values["star"] > values["safe"] > values["bench"]
    assert 0.5 * mu["star"] < p["safe"] * mu["safe"]  # p·μ would have ranked them the other way


def test_the_players_are_those_with_a_projection() -> None:
    assert sub_aware({1: 6.0}, {1: 1.0, 2: 0.5}, starters=1) == {1: 6.0}
