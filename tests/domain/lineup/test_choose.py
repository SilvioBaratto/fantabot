"""The one pure chain: candidates, Monte Carlo, the bench, the argmax. No I/O, no clock.

AD7 — live planning and the backtest both call `choose_plan`, so what is pinned here is
what the gate will grade. Four claims, and the first two are the ones a backtest cannot do
without:

* **same seed, identical plan.** Not "the same to three decimals": the same module, the same
  eleven, the same bench. A shadow report that cannot be recomputed is a shadow report that
  proves nothing;
* **the budget depends only on the inputs** — the module count and the roster size, both
  known before a candidate is built. A budget that read the candidate list would depend on
  the tilt grid the budget itself chooses;
* **a stop is a plan**, never an exception, because the hourly job has to field something;
* **ties break on discovery order**, so two candidates the draws cannot separate resolve the
  same way on every run.

The board is a 343 roster with a real bench, and `sigma_tilde = 0` wherever the assertion
has to be exact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pytest
from test_simulate import RULES

from fantabot.domain.lineup.choose import (
    MAX_DRAWS,
    MIN_DRAWS,
    Budget,
    PlanInputs,
    budget_for,
    choose_plan,
    draws_for,
    seed_for,
)
from fantabot.domain.lineup.dependence import POOLED, Dependence
from fantabot.domain.lineup.errors import NoFieldableModule
from fantabot.domain.lineup.models import RosterPlayer
from fantabot.domain.lineup.opponent import Opponent

ROLES: dict[int, frozenset[str]] = {
    0: frozenset({"POR"}), 1: frozenset({"POR"}),
    10: frozenset({"DC"}), 20: frozenset({"DC"}), 30: frozenset({"DC"}),
    3: frozenset({"DC"}), 13: frozenset({"DC"}),
    40: frozenset({"E"}), 70: frozenset({"E"}), 4: frozenset({"E"}),
    50: frozenset({"C"}), 60: frozenset({"C", "M"}), 5: frozenset({"C"}), 6: frozenset({"M"}),
    80: frozenset({"A"}), 90: frozenset({"A"}), 100: frozenset({"A"}), 2: frozenset({"A"}),
}
MACRO = {
    0: "GK", 1: "GK",
    10: "DEF", 20: "DEF", 30: "DEF", 3: "DEF", 13: "DEF",
    40: "DEF", 70: "DEF", 4: "DEF",
    50: "MID", 60: "MID", 5: "MID", 6: "MID",
    80: "ATT", 90: "ATT", 100: "ATT", 2: "ATT",
}
EVERYONE = tuple(ROLES)
DEPENDENCE = Dependence(
    rho={}, pearson={}, pairs={}, marginals={POOLED: np.array([-1.0, 1.0])}, pooled=0.0
)


def _inputs(
    *,
    mu: Mapping[int, float] | None = None,
    p: Mapping[int, float] | None = None,
    sigma: float = 0.0,
    modules: Sequence[str] = ("343",),
    bench_size: int = 5,
    roster: Sequence[int] = EVERYONE,
) -> PlanInputs:
    mus = dict.fromkeys(EVERYONE, 6.0) | dict(mu or {})
    ps = dict.fromkeys(EVERYONE, 1.0) | dict(p or {})
    value = {pid: ps[pid] * mus[pid] for pid in EVERYONE}
    return PlanInputs(
        roster=tuple(
            RosterPlayer(id=pid, roles=ROLES[pid], fvmma=value[pid]) for pid in roster
        ),
        modules=tuple(modules),
        mu=mus,
        p=ps,
        sigma_tilde=dict.fromkeys(EVERYONE, sigma),
        macro=MACRO,
        club=dict.fromkeys(EVERYONE, None),
        dependence=DEPENDENCE,
        rules=RULES,
        bench_size=bench_size,
        value=value,
        max_subs=5,
    )


SMALL = Budget(k=3, lambdas=(0.0,), node_budget=200, m=2, work=40_000)


def _plan(inputs: PlanInputs | None = None, *, seed: int = 5, **over: object):
    kwargs: dict[str, object] = {
        "rng": np.random.default_rng(seed),
        "budget": SMALL,
        "sub_mode": "basic",
        "opponent": None,
    }
    kwargs.update(over)
    return choose_plan(inputs or _inputs(), **kwargs)  # type: ignore[arg-type]


class TestTheSeed:
    def test_every_coordinate_moves_it(self) -> None:
        base = seed_for(4103937, 311681, 6)

        assert seed_for(4103938, 311681, 6) != base
        assert seed_for(4103937, 311682, 6) != base
        assert seed_for(4103937, 311681, 7) != base

    def test_it_survives_the_process(self) -> None:
        """Pinned to a literal, which is the only way to say "the same across processes":
        `hash()` is salted per run, so a seed taken from it agrees with itself inside one
        process and differs between two — exactly what an hourly job must not do.
        """
        assert seed_for(1, 2, 3) == 17799450507592347260
        assert 0 <= seed_for(1, 2, 3) < 2**64


class TestTheBudget:
    def test_it_reads_only_the_module_count_and_the_roster_size(self) -> None:
        """Both are known before a candidate is built, which is what makes the claim
        testable: change anything else and the budget must not move."""
        one = budget_for(_inputs())
        two = budget_for(_inputs(mu={80: 99.0}, p={80: 0.1}))

        assert one == two

    def test_more_modules_buy_fewer_candidates_each(self) -> None:
        eleven = budget_for(_inputs(modules=("343", "352", "3412", "3421", "3511",
                                             "4141", "4231", "4312", "433", "4411", "442")))
        one = budget_for(_inputs(modules=("343",)))

        assert eleven.m <= one.m

    def test_the_final_round_gets_what_the_screen_left_over(self) -> None:
        """Ten candidates screened at 400 draws is 4,000 units; the rest divides among the
        survivors. The screen is the cheap round on purpose — evaluating every candidate at
        the final draw count was what spent a whole wall clock on "stopped at 4/177"."""
        budget = Budget(k=1, lambdas=(0.0,), node_budget=1, m=4, work=60_000)

        draws, cut = draws_for(budget, candidates=10, bench_units=0, screen=400)

        assert draws == (60_000 - 10 * 400) // 4
        assert cut == ""

    def test_the_bench_search_is_charged_at_the_screen_price(self) -> None:
        """Ordering a bench is a comparison between benches, not a measurement of one, so
        it runs on the prefix — and the budget says so."""
        budget = Budget(k=1, lambdas=(0.0,), node_budget=1, m=4, work=60_000)

        without, cut_a = draws_for(budget, candidates=10, bench_units=0, screen=400)
        with_bench, cut_b = draws_for(budget, candidates=10, bench_units=10, screen=400)

        assert (cut_a, cut_b) == ("", "")
        assert without - with_bench == 4 * 10 * 400 // 4

    def test_a_clamp_is_recorded_rather_than_swallowed(self) -> None:
        """"The model wanted 20,000 draws and got 500" is the first thing to know when a
        plan looks noisy."""
        low, cut_low = draws_for(SMALL, candidates=10_000, bench_units=100)
        high, cut_high = draws_for(
            Budget(k=1, lambdas=(0.0,), node_budget=1, m=0, work=10**9), candidates=1,
            bench_units=0,
        )

        assert (low, high) == (MIN_DRAWS, MAX_DRAWS)
        assert cut_low.startswith("draws ") and cut_high.startswith("draws ")

    def test_a_plan_records_the_draws_it_took(self) -> None:
        plan = _plan()

        assert plan.draws >= MIN_DRAWS


class TestDeterminism:
    def test_the_same_seed_gives_the_same_plan(self) -> None:
        one, two = _plan(seed=5), _plan(seed=5)

        assert (one.module, one.starts, one.bench) == (two.module, two.starts, two.bench)
        assert one.evaluation == two.evaluation

    def test_a_different_seed_may_move_the_plan_but_never_the_shape(self) -> None:
        """The control: the chain is seeded, so two seeds are two runs — and both are still
        eleven players and a full bench."""
        one, two = _plan(seed=5), _plan(seed=6)

        assert len(one.starts) == len(two.starts) == 11
        assert len(one.bench) == len(two.bench) == 5

    def test_the_bench_and_the_eleven_are_disjoint(self) -> None:
        plan = _plan()

        assert not set(plan.starts) & set(plan.bench)

    def test_the_keeper_leads_the_bench(self) -> None:
        plan = _plan()

        assert "POR" in ROLES[plan.bench[0]]


class TestTheArgmax:
    def test_a_strictly_better_player_is_fielded(self) -> None:
        """`sigma_tilde = 0`, so the ranking is arithmetic and the assertion is exact."""
        plan = _plan(_inputs(mu={2: 20.0}))

        assert 2 in plan.starts

    def test_the_objective_is_named(self) -> None:
        assert _plan().objective == "fantapunti"

    def test_with_an_opponent_the_objective_is_league_points(self) -> None:
        opponent = Opponent(centres=np.array([60.0, 66.0, 72.0]), bandwidth=3.0)

        plan = _plan(opponent=opponent)

        assert plan.objective == "points"
        assert plan.evaluation.points is not None
        assert plan.evaluation.win is not None

    def test_a_roster_that_fields_nothing_is_refused(self) -> None:
        """No plan to degrade to: a caller handed an empty one would POST nothing and
        report success."""
        with pytest.raises(NoFieldableModule):
            _plan(_inputs(roster=(0, 1, 2, 3)))

    def test_the_runners_up_are_carried(self) -> None:
        plan = _plan(_inputs(modules=("343", "352")))

        assert plan.candidates >= len(plan.runners_up)


class TestAStopIsAPlan:
    def test_a_stop_before_the_first_candidate_still_fields_an_eleven(self) -> None:
        calls: list[int] = []

        def stop() -> bool:
            calls.append(1)
            return len(calls) > 1

        plan = _plan(should_stop=stop)

        assert plan.stopped
        assert len(plan.starts) == 11
        assert any("stopped at" in cut for cut in plan.cuts)

    def test_a_stop_that_leaves_nothing_evaluated_is_refused(self) -> None:
        """There is a difference between "we ran out of time" and "there is no XI": the
        first is a plan, the second cannot be."""
        with pytest.raises(NoFieldableModule):
            _plan(should_stop=lambda: True)

    def test_an_unstopped_run_is_not_marked_stopped(self) -> None:
        plan = _plan(should_stop=lambda: False)

        assert plan.stopped is False
        assert not any("stopped" in cut for cut in plan.cuts)


class TestTheBenchSearch:
    def test_the_top_candidates_get_the_monte_carlo_bench(self) -> None:
        plan = _plan()

        assert 1 <= plan.benched <= SMALL.m

    def test_a_candidate_whose_bench_cannot_be_filled_is_dropped(self) -> None:
        """A module this roster cannot bench is one the platform would refuse, so it is
        dropped like an unfieldable one — and when every candidate is dropped that way the
        answer is the same refusal an unfieldable roster gets, not an empty plan."""
        with pytest.raises(NoFieldableModule):
            _plan(_inputs(bench_size=99))

    def test_a_bench_search_can_be_stopped_and_the_plan_still_stands(self) -> None:
        calls: list[int] = []

        def stop() -> bool:
            calls.append(1)
            return len(calls) > 40

        plan = _plan(should_stop=stop)

        assert len(plan.starts) == 11 and len(plan.bench) == 5
