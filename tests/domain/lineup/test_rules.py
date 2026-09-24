"""`rules_from_calculate` over the three real `settings/calculate` bodies, and the maths on
top of them: band lookup, the substitution cap, the expected bonus. Pure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fantabot.domain.lineup.defence import expected_defence_bonus, p_defenders_voting
from fantabot.domain.lineup.rules import describe_bands, rules_from_calculate

FIXTURES = Path(__file__).parents[2] / "fixtures" / "lineup"


def _body(league: int) -> dict[str, Any]:
    return json.loads((FIXTURES / f"calculate_{league}.json").read_text())  # type: ignore[no-any-return]


def test_every_real_table_parses() -> None:
    for league in (2761635, 3677376, 4219373):
        rules = rules_from_calculate(_body(league))
        assert rules.defence is not None, league
        assert rules.subs is not None and rules.subs.max_subs == 5
        assert rules.problems == ()


def test_2761635_bands_and_keeper() -> None:
    d = rules_from_calculate(_body(2761635)).defence
    assert d is not None
    assert (d.lower, d.upper, d.includes_keeper) == (6.0, 7.5, True)
    assert d.bonus_for(5.99) == 0
    assert d.bonus_for(6.0) == 1
    assert d.bonus_for(6.24) == 1
    assert d.bonus_for(6.25) == 2
    assert d.bonus_for(7.49) == 4.5
    assert d.bonus_for(7.5) == 5
    assert d.bonus_for(9.0) == 5


def test_3677376_leaves_the_keeper_out_and_has_a_captain_modifier() -> None:
    rules = rules_from_calculate(_body(3677376))
    assert rules.defence is not None and rules.defence.includes_keeper is False
    assert rules.defence.bonus_for(7.25) == 4.5
    assert rules.captain is not None
    assert rules.captain.bonus_for(4.0) == -1.5
    assert rules.captain.bonus_for(7.5) == 1.5


def test_4219373_pays_nothing_in_its_first_band() -> None:
    d = rules_from_calculate(_body(4219373)).defence
    assert d is not None
    assert d.bonus_for(6.1) == 0.0  # [0, 0, 1, ...]: 6.00-6.24 still earns nothing
    assert d.bonus_for(6.25) == 1.0
    assert d.bonus_for(8.0) == 4.5


def test_a_null_modifier_means_the_lega_does_not_play_it() -> None:
    body = _body(2761635) | {"smodd": None}

    assert rules_from_calculate(body).defence is None


def test_a_table_of_the_wrong_length_is_not_guessed_at() -> None:
    body = _body(2761635)
    body["smodd"] = dict(body["smodd"], smodva=[0, 1, 2])

    rules = rules_from_calculate(body)

    assert rules.defence is None
    assert any("smodd" in p for p in rules.problems)


def test_a_missing_subst_is_named_not_raised() -> None:
    body = _body(2761635)
    body["subst"] = {"sstype": 3}

    rules = rules_from_calculate(body)

    assert rules.subs is None and any("subst" in p for p in rules.problems)


def test_describe_bands_reads_like_the_table() -> None:
    d = rules_from_calculate(_body(2761635)).defence
    assert d is not None

    labels = describe_bands(d)

    assert labels[0] == "<6.00: 0"
    assert labels[1] == "6.00+: 1"
    assert labels[-1] == "7.50+: 5"


def test_the_expected_bonus_rises_with_the_votes() -> None:
    d = rules_from_calculate(_body(2761635)).defence
    assert d is not None

    _, low = expected_defence_bonus(5.8, [5.8, 5.8, 5.8, 5.8], d)
    _, mid = expected_defence_bonus(6.5, [6.5, 6.5, 6.5, 6.5], d)
    _, high = expected_defence_bonus(7.6, [7.6, 7.6, 7.6, 7.6], d)

    assert low < mid < high <= 5.0


def test_only_the_three_best_defenders_are_averaged() -> None:
    d = rules_from_calculate(_body(3677376)).defence  # no keeper in the average here
    assert d is not None

    avg, _ = expected_defence_bonus(4.0, [7.0, 7.0, 7.0, 5.0], d)

    assert avg == pytest.approx(7.0)


def test_the_captain_table_reads_one_value_per_half_point() -> None:
    captain = rules_from_calculate(_body(3677376)).captain
    assert captain is not None

    assert [captain.bonus_for(v) for v in (4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 9.0)] == [
        -1.5, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 1.5,
    ]
    assert captain.expected(6.0, 0.0) == 0.0
    assert captain.expected(7.0, 0.75) > captain.expected(6.0, 0.75)


def test_the_substitution_cap_can_leave_a_defender_uncovered() -> None:
    # 4 D starters, the 4th sure to miss; a sure bench D. Three attackers also miss.
    starters, bench = [1.0, 1.0, 1.0, 0.0], [1.0]
    others_out = [0.0, 0.0, 0.0]

    assert p_defenders_voting(starters, bench) == pytest.approx(1.0)
    assert p_defenders_voting(starters, bench, max_subs=5, others=others_out) == pytest.approx(1.0)
    assert p_defenders_voting(starters, bench, max_subs=3, others=others_out) == pytest.approx(0.0)
