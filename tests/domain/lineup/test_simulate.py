"""The evaluator: one candidate XI against the analytic opponent. Pure, seeded, vectorised.

Nothing here pins a Monte Carlo number. Three shapes of assertion instead:

* **same seed, bit-identical** — the property the backtest and the shadow report both need,
  and the one an unseeded `default_rng` silently breaks;
* **the analytic limit** — with `sigma_tilde = 0` and `p = 1` there is no randomness left,
  so the evaluator must reproduce a closed form to 1e-9. That is the test that would catch
  a ladder read off the wrong edges, a malus applied twice, or an opponent table shifted by
  one goal, none of which a "roughly right" mean would show;
* **the invariances** — two identical lineups are two identical evaluations, and a bank is a
  function of its seed.

The board is deliberately small and its roles are unambiguous, so the substitution engine's
answer is readable by hand: 343 needs `POR, {B,DC}, DC, DC, E, C, {C,M}, E, {A,W}, {A,PC},
{A,W}`, and the bench holds one of each thing that can come on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import numpy as np
import pytest

from fantabot.domain.lineup.dependence import POOLED, Dependence, Member
from fantabot.domain.lineup.scoring import ScoringRules, goals
from fantabot.domain.lineup.simulate import (
    DrawBank,
    build_bank,
    evaluate,
    ladder_goals,
)
from fantabot.domain.lineup.substitution import SubstitutionEngine

MODULE = "343"
STARTS = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
BENCH = (1, 2, 3, 4, 5)
ROLES: dict[int, frozenset[str]] = {
    0: frozenset({"POR"}),
    10: frozenset({"DC"}), 20: frozenset({"DC"}), 30: frozenset({"DC"}),
    40: frozenset({"E"}), 70: frozenset({"E"}),
    50: frozenset({"C"}), 60: frozenset({"C", "M"}),
    80: frozenset({"A"}), 90: frozenset({"A"}), 100: frozenset({"A"}),
    1: frozenset({"POR"}), 2: frozenset({"A"}), 3: frozenset({"DC"}),
    4: frozenset({"E"}), 5: frozenset({"C"}),
}
EVERYONE = (*STARTS, *BENCH)
MACRO = {
    0: "GK", 1: "GK",
    10: "DEF", 20: "DEF", 30: "DEF", 3: "DEF",
    40: "DEF", 70: "DEF", 4: "DEF",
    50: "MID", 60: "MID", 5: "MID",
    80: "ATT", 90: "ATT", 100: "ATT", 2: "ATT",
}

#: The lega's own ladder, from `settings/calculate` (`stlmt` 66, `stgoal` 6..42).
RULES = ScoringRules(
    goal=3.0, assist=1.0, yellow=-0.5, red=-1.0, own_goal=-2.0, penalty_scored=3.0,
    penalty_missed=-3.0, penalty_saved=3.0, conceded=-1.0, motm=1.0, clean_sheet=1.0,
    decisive_goal=1.0, threshold=66.0, steps=(6.0, 12.0, 18.0, 24.0, 30.0, 36.0, 42.0),
)


def _dependence(spread: float = 1.0) -> Dependence:
    """Independent players with a symmetric two-point marginal, so a draw is `mu ± sigma`."""
    grid = np.array([-spread, spread], dtype=np.float64)
    return Dependence(
        rho={}, pearson={}, pairs={}, marginals={POOLED: grid}, pooled=0.0
    )


def _members(mu: Mapping[int, float], sigma: Mapping[int, float]) -> list[Member]:
    return [
        Member(macro=MACRO[pid], club=None, mu=mu[pid], sigma_tilde=sigma[pid])
        for pid in EVERYONE
    ]


def _bank(
    *, mu: float = 6.0, sigma: float = 0.0, p: float = 1.0, n: int = 64, seed: int = 7
) -> DrawBank:
    mus = dict.fromkeys(EVERYONE, mu)
    sigmas = dict.fromkeys(EVERYONE, sigma)
    return build_bank(
        _members(mus, sigmas),
        EVERYONE,
        dict.fromkeys(EVERYONE, p),
        _dependence(),
        rng=np.random.default_rng(seed),
        n=n,
    )


def _engine(starts: Sequence[int] = STARTS, bench: Sequence[int] = BENCH) -> SubstitutionEngine:
    return SubstitutionEngine(
        module=MODULE, starts=tuple(starts), bench=tuple(bench), roles=ROLES,
        mode="basic", max_subs=5,
    )


#: A three-goal opponent, certain. The simplest analytic control there is.
def _certain(goals_scored: int, rungs: int = 9) -> list[float]:
    table = [0.0] * rungs
    table[goals_scored] = 1.0
    return table


class TestTheLadder:
    @pytest.mark.parametrize("total", [0.0, 65.9, 66.0, 71.9, 72.0, 108.0, 200.0])
    def test_it_agrees_with_the_scalar_ladder_everywhere(self, total: float) -> None:
        """`scoring.goals` is the definition; this is the same function over an array, and
        a vectorised copy that drifts is a plan scored on a ladder nobody declared."""
        vector = ladder_goals(np.array([total]), rules=RULES)

        assert vector[0] == goals(total, threshold=RULES.threshold, steps=RULES.steps)

    def test_steps_declared_out_of_order_are_still_a_ladder(self) -> None:
        """`searchsorted` needs them ascending. A lega that declared them otherwise would
        otherwise be scored on a silently wrong ladder rather than refused."""
        shuffled = replace(RULES, steps=(12.0, 6.0, 42.0, 18.0))

        assert ladder_goals(np.array([66.0 + 12.0]), rules=shuffled)[0] == 3


class TestTheBank:
    def test_the_same_seed_gives_the_same_bank(self) -> None:
        one, two = _bank(sigma=1.0, p=0.7), _bank(sigma=1.0, p=0.7)

        assert np.array_equal(one.scores, two.scores)
        assert np.array_equal(one.present, two.present)

    def test_a_different_seed_gives_a_different_bank(self) -> None:
        """The control: without it, "same seed, same answer" would also pass on a constant."""
        one, two = _bank(sigma=1.0, p=0.7, seed=7), _bank(sigma=1.0, p=0.7, seed=8)

        assert not np.array_equal(one.present, two.present)

    def test_certain_presence_is_everybody_every_draw(self) -> None:
        assert _bank(p=1.0).present.all()

    def test_certain_absence_is_nobody(self) -> None:
        assert not _bank(p=0.0).present.any()

    def test_no_spread_is_the_mean_exactly(self) -> None:
        bank = _bank(mu=6.5, sigma=0.0)

        assert np.allclose(bank.scores, 6.5, atol=0.0, rtol=0.0)

    def test_a_member_list_that_is_not_the_player_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="one list"):
            build_bank(
                _members(dict.fromkeys(EVERYONE, 6.0), dict.fromkeys(EVERYONE, 0.0)),
                EVERYONE[:-1],
                dict.fromkeys(EVERYONE, 1.0),
                _dependence(),
                rng=np.random.default_rng(1),
                n=4,
            )

    @pytest.mark.parametrize("n", [0, -1])
    def test_a_bank_of_no_draws_is_refused(self, n: int) -> None:
        with pytest.raises(ValueError, match="at least one draw"):
            _bank(n=n)


class TestTheAnalyticLimit:
    """`sigma_tilde = 0` and `p = 1`: nothing is random, so the evaluator must reproduce the
    closed form exactly. Every piece is in it — the eleven summed, the ladder, the opponent's
    table — so a ladder off by an edge or a malus applied twice fails here and nowhere else.
    """

    def test_the_total_is_the_eleven_summed(self) -> None:
        evaluation = evaluate(_bank(mu=6.0, sigma=0.0, p=1.0), engine=_engine(), rules=RULES)

        assert evaluation.fantapunti == pytest.approx(11 * 6.0, abs=1e-9)
        assert evaluation.fantapunti_sd == pytest.approx(0.0, abs=1e-9)

    def test_the_goals_are_the_ladder_of_that_total(self) -> None:
        evaluation = evaluate(_bank(mu=6.5, sigma=0.0, p=1.0), engine=_engine(), rules=RULES)

        assert evaluation.goals == pytest.approx(
            goals(11 * 6.5, threshold=RULES.threshold, steps=RULES.steps), abs=1e-9
        )

    @pytest.mark.parametrize(("mu", "opponent", "points"), [
        (6.5, 0, 3.0),   # 71.5 -> 1 goal, he scores none: a win
        (6.5, 1, 1.0),   # the same goal each: a draw
        (6.5, 2, 0.0),   # he scores two: a loss
        (5.0, 0, 1.0),   # 55 -> no goal, and neither does he
    ])
    def test_the_league_points_are_exact(self, mu: float, opponent: int, points: float) -> None:
        evaluation = evaluate(
            _bank(mu=mu, sigma=0.0, p=1.0),
            engine=_engine(), rules=RULES, opponent_goals=_certain(opponent),
        )

        assert evaluation.points == pytest.approx(points, abs=1e-9)
        assert (evaluation.win, evaluation.drawn, evaluation.loss) == pytest.approx(
            (float(points == 3.0), float(points == 1.0), float(points == 0.0)), abs=1e-9
        )

    def test_a_spread_opponent_is_folded_in_exactly(self) -> None:
        """Not a certainty this time: the whole table has to be read, and read at the right
        goal — an opponent table shifted by one rung passes every certain case above."""
        table = [0.2, 0.5, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        evaluation = evaluate(
            _bank(mu=6.5, sigma=0.0, p=1.0),
            engine=_engine(), rules=RULES, opponent_goals=table,
        )

        assert evaluation.win == pytest.approx(0.2, abs=1e-9)
        assert evaluation.drawn == pytest.approx(0.5, abs=1e-9)
        assert evaluation.loss == pytest.approx(0.3, abs=1e-9)
        assert evaluation.points == pytest.approx(3 * 0.2 + 0.5, abs=1e-9)

    def test_a_malus_is_one_point_off_the_team_total(self) -> None:
        """Measured on the platform's own lines (T12): `cscr = scr + bonuses - m`. Both `E`
        are missing; the bench holds one `E` and one centre-back, so the Adapted tier fields
        eleven for exactly one malus — and the total is a point short of eleven men's."""
        bank = _bank(mu=6.0, sigma=0.0, p=1.0)
        absent = np.array([bank.index[40], bank.index[70]], dtype=np.intp)
        present = bank.present.copy()
        present[:, absent] = False
        thinned = DrawBank(bank.player_ids, bank.scores, present)

        evaluation = evaluate(thinned, engine=_engine(bench=(1, 3, 4)), rules=RULES)

        assert evaluation.malus == pytest.approx(1.0, abs=1e-9)
        assert evaluation.fantapunti == pytest.approx(11 * 6.0 - 1.0, abs=1e-9)

    def test_a_man_short_is_one_player_fewer_in_the_sum(self) -> None:
        bank = _bank(mu=6.0, sigma=0.0, p=1.0)
        present = bank.present.copy()
        present[:, [bank.index[20], bank.index[30]]] = False
        present[:, [bank.index[p] for p in (2, 4, 5)]] = False
        thinned = DrawBank(bank.player_ids, bank.scores, present)

        evaluation = evaluate(thinned, engine=_engine(bench=(1, 3)), rules=RULES)

        assert evaluation.short == pytest.approx(1.0, abs=1e-9)
        assert evaluation.fantapunti == pytest.approx(10 * 6.0, abs=1e-9)


class TestTheInvariances:
    def test_the_same_bank_and_lineup_give_the_same_evaluation(self) -> None:
        bank = _bank(mu=6.0, sigma=1.0, p=0.8)

        assert evaluate(bank, engine=_engine(), rules=RULES) == evaluate(
            bank, engine=_engine(), rules=RULES
        )

    def test_two_seeds_are_two_answers(self) -> None:
        """The control for the identity above."""
        one = evaluate(_bank(sigma=1.0, p=0.8, seed=7), engine=_engine(), rules=RULES)
        two = evaluate(_bank(sigma=1.0, p=0.8, seed=8), engine=_engine(), rules=RULES)

        assert one.fantapunti != two.fantapunti

    def test_common_random_numbers_compare_two_candidates_on_one_week(self) -> None:
        """The point of the shared bank: the difference between two XIs must come from the
        XIs. A better man in the eleven is better on *every* draw, not on average."""
        mus = dict.fromkeys(EVERYONE, 6.0) | {2: 9.0}
        bank = build_bank(
            _members(mus, dict.fromkeys(EVERYONE, 0.0)),
            EVERYONE, dict.fromkeys(EVERYONE, 1.0), _dependence(),
            rng=np.random.default_rng(3), n=32,
        )
        swapped = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 2)

        base = evaluate(bank, engine=_engine(), rules=RULES)
        better = evaluate(
            bank, engine=_engine(starts=swapped, bench=(1, 3, 4, 5, 100)), rules=RULES
        )

        assert better.fantapunti - base.fantapunti == pytest.approx(3.0, abs=1e-9)

    def test_a_player_outside_the_bank_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not in the draw bank"):
            evaluate(_bank(), engine=_engine(bench=(1, 2, 3, 4, 999)), rules=RULES)

    def test_an_opponent_table_shorter_than_the_ladder_is_refused(self) -> None:
        """Two tables built from different `steps` would silently score the wrong league."""
        with pytest.raises(ValueError, match="different `steps`"):
            evaluate(
                _bank(mu=12.0, sigma=0.0, p=1.0),
                engine=_engine(), rules=RULES, opponent_goals=[1.0, 0.0],
            )

    def test_without_an_opponent_there_is_no_result_and_the_totals_stand(self) -> None:
        """Inventing a uniform opponent would be a made-up league; the caller ranks on
        fantapunti instead and records that it did."""
        evaluation = evaluate(_bank(mu=6.0, sigma=0.0, p=1.0), engine=_engine(), rules=RULES)

        assert (evaluation.points, evaluation.win, evaluation.drawn, evaluation.loss) == (
            None, None, None, None
        )
        assert evaluation.fantapunti == pytest.approx(66.0, abs=1e-9)


class TestThePatternGrouping:
    def test_a_certain_week_is_one_pattern(self) -> None:
        """The engine is called once per *distinct* absence pattern, not once per draw."""
        assert evaluate(_bank(p=1.0, n=64), engine=_engine(), rules=RULES).patterns == 1

    def test_a_random_week_is_many(self) -> None:
        evaluation = evaluate(_bank(sigma=1.0, p=0.7, n=64), engine=_engine(), rules=RULES)

        assert 1 < evaluation.patterns <= 64
