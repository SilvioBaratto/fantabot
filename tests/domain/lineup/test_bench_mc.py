"""The bench under Monte Carlo: chosen for what it covers, not for what it is worth.

`bench.py` ranks the reserves by value, which is right when every vacancy is
interchangeable and wrong when it is not — the auto-sub engine tests combinations in bench
order, so the second-best outfielder is worth nothing if he cannot cover the slot that
empties. Two claims are pinned here and the first is the one that can go vacuous:

* **never worse than `order_bench` under the same draws.** A test that compares two benches
  under draws where nobody comes on proves nothing at all, so every case below **forces**
  substitutions by marking starters absent in the bank itself;
* **slot 0 is a reserve keeper**, always, because the platform settles him before any
  combination is searched and a bench whose first man is not one is refused.

Determinism is the third: two reserves the draws cannot tell apart order by the fallback's
own ranking and then by id, so a shadow report can be recomputed.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest
from test_simulate import MACRO, RULES  # noqa: F401 - the shared board

from fantabot.domain.lineup.bench_mc import BenchOrder, order_bench_mc, work_units
from fantabot.domain.lineup.dependence import POOLED, Dependence, Member
from fantabot.domain.lineup.errors import BenchIncomplete
from fantabot.domain.lineup.simulate import DrawBank, build_bank, evaluate
from fantabot.domain.lineup.substitution import SubstitutionEngine

MODULE = "343"
STARTS = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
#: Two keepers, an `E`, a centre-back, a forward and a regista: one of everything that can
#: come on, so which of them is chosen is a statement about cover and not about supply.
RESERVES = (1, 7, 4, 3, 2, 6)
ROLES: dict[int, frozenset[str]] = {
    0: frozenset({"POR"}),
    10: frozenset({"DC"}), 20: frozenset({"DC"}), 30: frozenset({"DC"}),
    40: frozenset({"E"}), 70: frozenset({"E"}),
    50: frozenset({"C"}), 60: frozenset({"C", "M"}),
    80: frozenset({"A"}), 90: frozenset({"A"}), 100: frozenset({"A"}),
    1: frozenset({"POR"}), 7: frozenset({"POR"}),
    4: frozenset({"E"}), 3: frozenset({"DC"}), 2: frozenset({"A"}), 6: frozenset({"M"}),
}
EVERYONE = (*STARTS, *RESERVES)
ROLE_MACRO = {
    0: "GK", 1: "GK", 7: "GK",
    10: "DEF", 20: "DEF", 30: "DEF", 3: "DEF", 40: "DEF", 70: "DEF", 4: "DEF",
    50: "MID", 60: "MID", 6: "MID",
    80: "ATT", 90: "ATT", 100: "ATT", 2: "ATT",
}


def _bank(*, mu: dict[int, float] | None = None, absent: Sequence[int] = ()) -> DrawBank:
    """A certain week — no spread, everybody present — with `absent` forced out of it.

    Certainty is deliberate: the two orderings are then compared on an exact number rather
    than on a mean, so a difference of 0.001 is a real difference and not noise.
    """
    mus = dict.fromkeys(EVERYONE, 6.0) | (mu or {})
    members = [
        Member(macro=ROLE_MACRO[pid], club=None, mu=mus[pid], sigma_tilde=0.0)
        for pid in EVERYONE
    ]
    bank = build_bank(
        members, EVERYONE, dict.fromkeys(EVERYONE, 1.0),
        Dependence(rho={}, pearson={}, pairs={}, marginals={POOLED: np.array([-1.0, 1.0])},
                   pooled=0.0),
        rng=np.random.default_rng(11), n=16,
    )
    if not absent:
        return bank
    present = bank.present.copy()
    present[:, [bank.index[pid] for pid in absent]] = False
    return DrawBank(bank.player_ids, bank.scores, present)


def _engine_for(bench: Sequence[int]) -> SubstitutionEngine:
    return SubstitutionEngine(
        module=MODULE, starts=STARTS, bench=tuple(bench), roles=ROLES, mode="basic", max_subs=5
    )


def _order(bank: DrawBank, *, fallback: Sequence[int], size: int = 3) -> BenchOrder:
    return order_bench_mc(
        bank, engine_for=_engine_for, reserves=RESERVES, roles=ROLES, size=size,
        rules=RULES, fallback=fallback,
    )


class TestTheKeeperTakesSlotZero:
    def test_the_first_man_is_always_a_reserve_keeper(self) -> None:
        order = _order(_bank(absent=(40,)), fallback=(1, 4, 3))

        assert "POR" in ROLES[order.bench[0]]

    def test_a_bench_with_no_reserve_keeper_is_refused(self) -> None:
        with pytest.raises(BenchIncomplete, match="goalkeeper"):
            order_bench_mc(
                _bank(), engine_for=_engine_for, reserves=(4, 3, 2), roles=ROLES, size=3,
                rules=RULES, fallback=(4, 3, 2),
            )

    def test_the_better_of_two_keepers_is_taken(self) -> None:
        """Both cover slot 0, so the choice is the evaluator's: the one who scores more."""
        bank = _bank(mu={7: 9.0}, absent=(0,))

        order = _order(bank, fallback=(1, 4, 3))

        assert order.bench[0] == 7

    def test_too_few_reserves_is_refused(self) -> None:
        with pytest.raises(BenchIncomplete, match="need"):
            order_bench_mc(
                _bank(), engine_for=_engine_for, reserves=(1, 4), roles=ROLES, size=3,
                rules=RULES, fallback=(1, 4, 3),
            )


class TestNeverWorse:
    def test_the_chosen_bench_is_never_worse_than_the_fallback(self) -> None:
        """Forced substitutions, so the bench is actually used — without them both orders
        score identically and the claim would hold vacuously. Measured: 65.0 against the
        fallback's 59.0, because `order_bench`'s two best reserves cover neither flank."""
        bank = _bank(absent=(40, 70))
        fallback = (1, 2, 6)

        order = _order(bank, fallback=fallback)
        baseline = evaluate(bank, engine=_engine_for(fallback), rules=RULES)

        assert order.evaluation.fantapunti >= baseline.fantapunti
        assert order.evaluation.fantapunti > baseline.fantapunti

    def test_a_bench_that_cannot_cover_is_beaten_by_one_that_can(self) -> None:
        """The whole point. `order_bench` would field the two highest-valued reserves; both
        `E` are missing and only the `E` and the centre-back can cover them, so a
        value-ranked bench plays a man short and the greedy does not."""
        bank = _bank(mu={2: 9.0, 6: 8.0}, absent=(40, 70))

        greedy = _order(bank, fallback=(1, 2, 6))
        value = order_bench_mc(
            bank, engine_for=_engine_for, reserves=(1, 2, 6), roles=ROLES, size=3,
            rules=RULES, fallback=(1, 2, 6),
        )

        assert greedy.evaluation.short < value.evaluation.short
        assert greedy.evaluation.fantapunti > value.evaluation.fantapunti

    def test_the_value_ordering_is_kept_when_the_greedy_does_not_beat_it(self) -> None:
        """The guarantee is a comparison, not a hope: a greedy has no optimality argument,
        so its answer is measured against the baseline and the better of the two returned."""
        bank = _bank()

        order = _order(bank, fallback=(1, 4, 3))

        assert order.source in {"greedy", "value"}
        assert order.evaluation.fantapunti >= 11 * 6.0

    def test_a_fallback_of_the_wrong_length_is_refused(self) -> None:
        with pytest.raises(ValueError, match="fallback bench"):
            _order(_bank(), fallback=(1, 4), size=3)

    def test_a_bench_of_no_men_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least the keeper"):
            _order(_bank(), fallback=(), size=0)


class TestDeterminism:
    def test_a_tie_breaks_on_the_fallback_order(self) -> None:
        """Every outfield reserve scores the same and covers nothing, so the draws cannot
        separate them and the fallback's own ranking decides — not dict order, and not
        whichever `max` happened to see first."""
        bank = _bank()

        first = _order(bank, fallback=(1, 2, 6))
        second = _order(bank, fallback=(1, 6, 2))

        assert first.bench[1] == 2
        assert second.bench[1] == 6


class TestTheWorkBound:
    def test_the_bound_is_never_under_what_was_spent(self) -> None:
        """T30 refuses a bench search it cannot afford; a bound that under-counted would
        commit to work it then abandons half done."""
        order = _order(_bank(absent=(40, 70)), fallback=(1, 2, 6))

        assert order.work <= work_units(pool=len(RESERVES), size=3)

    def test_the_bound_grows_with_the_bench(self) -> None:
        assert work_units(pool=12, size=5) > work_units(pool=12, size=3)
