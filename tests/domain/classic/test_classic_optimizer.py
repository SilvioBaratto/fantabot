"""Classic roster optimizer: per-role bands honoured, no schema seed, fail-closed infeasible.

The Classic fill shares the mean-variance spine with Mantra but replaces the two-super-role
composition + L1 legal-XI seed with four per-role bands and count-forcing. `legality={}` is
passed throughout: a Classic build must never consult a schema.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.optimizer import InfeasibleRoster, optimize_roster
from fantabot.domain.asta.state import AstaState
from fantabot.domain.asta.value import NaiveValueModel
from fantabot.domain.classic.roles import ClassicPlayer
from fantabot.domain.classic.state import ClassicRosterRules

# A small band: 1 of each role, size 4. Two candidates per role so the optimizer must choose.
SMALL = ClassicRosterRules(size=4, bands=(("P", 1, 1), ("D", 1, 1), ("C", 1, 1), ("A", 1, 1)))
POOL = [
    ClassicPlayer("p1", "P"), ClassicPlayer("p2", "P"),
    ClassicPlayer("d1", "D"), ClassicPlayer("d2", "D"),
    ClassicPlayer("c1", "C"), ClassicPlayer("c2", "C"),
    ClassicPlayer("a1", "A"), ClassicPlayer("a2", "A"),
]
TEAMS = {p.id: p.id.upper() for p in POOL}  # all distinct clubs
PRICES = {p.id: 10.0 for p in POOL}
VALUE = NaiveValueModel(
    signals={"p1": 5.0, "p2": 3.0, "d1": 9.0, "d2": 2.0, "c1": 8.0, "c2": 2.0,
             "a1": 7.0, "a2": 2.0},
    prior_mean=1.0, base_variance=4.0, no_history_variance=4.0,
)


def _counts(role_of, ids):
    out = {"P": 0, "D": 0, "C": 0, "A": 0}
    for pid in ids:
        out[role_of[pid]] += 1
    return out


def _optimize(*, rules=SMALL, pool=POOL, budget=100.0, lam=0.0, state=None):
    return optimize_roster(
        state or AstaState(total_budget=budget),
        pool,
        value=VALUE, prices=PRICES, teams=TEAMS,
        legality={},  # a Classic build must not need a schema
        rules=rules, lam=lam,
    )


def test_classic_roster_meets_every_band() -> None:
    r = _optimize().optimal
    assert len(r) == 4
    role_of = {p.id: p.role for p in POOL}
    assert _counts(role_of, r.player_ids) == {"P": 1, "D": 1, "C": 1, "A": 1}
    # highest-value candidate per band, all clubs distinct so no covariance tie-break
    assert set(r.player_ids) == {"p1", "d1", "c1", "a1"}


def test_the_full_confirmed_band() -> None:
    # [3,8,8,6] over 25, the real lega 3584692 shape — enough candidates per role.
    pool, prices, signals = [], {}, {}
    for role, n in (("P", 4), ("D", 12), ("C", 12), ("A", 9)):
        for i in range(n):
            pid = f"{role}{i}"
            pool.append(ClassicPlayer(pid, role))
            prices[pid] = 5.0
            signals[pid] = float(n - i)  # descending value within the role
    value = NaiveValueModel(signals=signals, prior_mean=1.0, base_variance=4.0,
                            no_history_variance=4.0)
    teams = {p.id: p.id for p in pool}
    r = optimize_roster(
        AstaState(total_budget=500.0), pool, value=value, prices=prices, teams=teams,
        legality={}, rules=ClassicRosterRules(), lam=0.0,
    ).optimal
    assert len(r) == 25
    role_of = {p.id: p.role for p in pool}
    assert _counts(role_of, r.player_ids) == {"P": 3, "D": 8, "C": 8, "A": 6}


def test_a_missing_role_is_infeasible() -> None:
    # no attackers in the pool -> the A band cannot be met.
    pool = [p for p in POOL if p.role != "A"]
    with pytest.raises(InfeasibleRoster):
        _optimize(pool=pool)


def test_budget_that_cannot_fill_the_band_is_infeasible() -> None:
    with pytest.raises(InfeasibleRoster):
        _optimize(budget=3.0)  # 4 players at >=1 credit each, but the cheapest is 10
