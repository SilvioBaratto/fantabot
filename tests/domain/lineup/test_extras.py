"""Captain, vice and switch: the `lcap`/`lswi` gates and the choice. Pure."""

from __future__ import annotations

from fantabot.domain.lineup.extras import (
    captain_slots,
    captain_value,
    choose_captains,
    choose_switch,
    switch_enabled,
)
from fantabot.domain.lineup.predict import Prediction
from fantabot.domain.lineup.rules import BandedModifier


def _p(pid: int, p_play: float, expected: float) -> Prediction:
    return Prediction(
        pid=pid, p_play=p_play, fv_if_plays=expected, expected=expected, score=expected,
        factors={},
    )


def test_only_measured_lcap_values_send_a_captain() -> None:
    assert captain_slots(2) == 2  # 3677376: captain + vice
    assert captain_slots(3) == 0  # 2761635: no captain
    assert captain_slots(1) == 0  # unmeasured -> nothing guessed
    assert captain_slots(None) == 0


def test_only_measured_lswi_values_send_a_switch() -> None:
    assert switch_enabled(2) and switch_enabled(3)
    assert not switch_enabled(1)
    assert not switch_enabled(None)


def test_captain_then_vice_by_expected_fantavoto() -> None:
    preds = {1: _p(1, 0.9, 5.0), 2: _p(2, 0.8, 8.0), 3: _p(3, 0.9, 7.0)}

    assert choose_captains([1, 2, 3], preds, slots=2) == (2, 3)
    assert choose_captains([1, 2, 3], preds, slots=0) == ()


def _voted(pid: int, p_play: float, vote: float, expected: float = 6.0) -> Prediction:
    return Prediction(
        pid=pid, p_play=p_play, fv_if_plays=expected, expected=p_play * expected,
        score=expected, factors={}, vote_if_plays=vote,
    )


def _captain_table() -> BandedModifier:
    # 3677376's `smodcp`: -1.5 at <= 4.5 … +1.5 at >= 7.5, one value per half point.
    import json
    from pathlib import Path

    from fantabot.domain.lineup.rules import rules_from_calculate

    path = Path(__file__).parents[2] / "fixtures" / "lineup" / "calculate_3677376.json"
    captain = rules_from_calculate(json.loads(path.read_text())).captain
    assert captain is not None
    return captain


def test_with_a_captain_modifier_the_vote_decides_not_the_fantavoto() -> None:
    # 1: a forward with a big fantavoto but an ordinary vote; 2: a steady 7-vote player.
    preds = {1: _voted(1, 0.9, 6.0, expected=12.0), 2: _voted(2, 0.9, 7.0, expected=7.0)}

    assert choose_captains([1, 2], preds, slots=1, modifier=_captain_table()) == (2,)
    assert choose_captains([1, 2], preds, slots=1) == (1,)  # no modifier: fantavoto


def test_without_a_vice_a_doubtful_captain_loses_to_a_sure_one() -> None:
    preds = {1: _voted(1, 0.3, 7.5), 2: _voted(2, 0.95, 6.75)}

    assert choose_captains([1, 2], preds, slots=1, modifier=_captain_table()) == (2,)


def test_a_sure_vice_makes_a_doubtful_high_vote_captain_worth_it() -> None:
    # 0.3 * E(7.5) + 0.7 * 0.95 * E(6.75)  >  0.95 * E(6.75) + 0.05 * 0.3 * E(7.5)
    preds = {1: _voted(1, 0.3, 7.5), 2: _voted(2, 0.95, 6.75), 3: _voted(3, 0.95, 6.5)}

    assert choose_captains([1, 2, 3], preds, slots=2, modifier=_captain_table()) == (1, 2)


def test_the_vice_only_counts_when_the_captain_misses() -> None:
    table = _captain_table()
    preds = {1: _voted(1, 1.0, 7.0), 2: _voted(2, 1.0, 4.0)}

    # a captain who always plays makes the vice irrelevant, even a terrible one
    assert captain_value(1, 2, preds, table) == captain_value(1, None, preds, table)


def test_a_bench_player_is_never_captain() -> None:
    preds = {1: _p(1, 0.9, 5.0), 9: _p(9, 0.9, 20.0)}

    assert choose_captains([1], preds, slots=2) == (1,)


def test_the_switch_covers_the_riskiest_starter_with_a_same_role_reserve() -> None:
    roles = {1: "D", 2: "A", 10: "D", 11: "A", 12: "A"}
    preds = {
        1: _p(1, 0.9, 6.0),
        2: _p(2, 0.5, 7.0),  # the riskiest starter, an A
        10: _p(10, 0.9, 6.5),
        11: _p(11, 0.7, 5.0),
        12: _p(12, 0.8, 6.0),  # the best A in reserve
    }

    assert choose_switch([1, 2], [10, 11, 12], roles, preds) == (2, 12)


def test_no_same_role_reserve_moves_to_the_next_riskiest_starter() -> None:
    roles = {1: "D", 2: "P", 10: "D"}
    preds = {1: _p(1, 0.9, 6.0), 2: _p(2, 0.4, 5.0), 10: _p(10, 0.9, 6.5)}

    assert choose_switch([1, 2], [10], roles, preds) == (1, 10)


def test_a_reserve_expected_to_score_nothing_is_never_named() -> None:
    roles = {1: "C", 10: "C"}
    preds = {1: _p(1, 0.5, 6.0), 10: _p(10, 0.0, 0.0)}

    assert choose_switch([1], [10], roles, preds) is None
