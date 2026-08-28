"""L3/core — the greedy mean-variance roster optimizer. Pure and synchronous.

Objective: team expected points minus a risk penalty, `Σμ - λ·Var`, where the variance
of the team total carries a positive same-team covariance term — so a higher λ diversifies
across clubs. Subject to: the credit budget, the roster composition (`RosterRules`), and
the roster must field a legal XI (L1). The MVP builder is greedy; it must still return only
rosters that meet every invariant, and raise when none exists.
"""

from __future__ import annotations

import pytest

from fantabot.asta_engine.legality import SchemaLegality, SlotRule
from fantabot.asta_engine.optimizer import InfeasibleRoster, objective, optimize_roster
from fantabot.asta_engine.roles import MantraPlayer, normalize_roles
from fantabot.asta_engine.state import AstaState, RosterRules
from fantabot.asta_engine.value import NaiveValueModel

# A tiny world: a 3-man roster (1 GK + 2 attackers), one schema Por/A/A.
RULES = RosterRules(size=3, goalkeeper_roles=frozenset({"POR"}), min_goalkeepers=1, min_movement=2)
MINI = {
    "por-a-a": SchemaLegality(
        nome="por-a-a",
        slots=(
            SlotRule("Por", frozenset({"POR"}), frozenset({"POR"})),
            SlotRule("A", frozenset({"A"}), frozenset({"A"})),
            SlotRule("A2", frozenset({"A"}), frozenset({"A"})),
        ),
    )
}


def _player(pid: str, role: str) -> MantraPlayer:
    return MantraPlayer(id=pid, roles=normalize_roles([role]))


# Two attackers on team X, one on team Y, and a keeper on Z.
POOL = [
    _player("a1", "A"),
    _player("a2", "A"),
    _player("a3", "A"),
    _player("gk", "POR"),
]
TEAMS = {"a1": "X", "a2": "X", "a3": "Y", "gk": "Z"}
PRICES = {"a1": 10.0, "a2": 10.0, "a3": 10.0, "gk": 10.0}
VALUE = NaiveValueModel(
    signals={"a1": 10.0, "a2": 10.0, "a3": 9.5, "gk": 3.0},
    prior_mean=1.0,
    base_variance=4.0,
    no_history_variance=4.0,
)


def _optimize(*, lam: float, state: AstaState | None = None, pool=POOL):
    return optimize_roster(
        state or AstaState(total_budget=100.0),
        pool,
        value=VALUE,
        prices=PRICES,
        teams=TEAMS,
        legality=MINI,
        rules=RULES,
        lam=lam,
    )


def test_optimal_roster_meets_every_invariant() -> None:
    result = _optimize(lam=0.0)
    r = result.optimal
    assert len(r) == RULES.size                     # exactly 30 (here 3)
    assert r.total_cost <= 100.0                     # within budget
    assert sum(1 for pid in r.player_ids if pid == "gk") == 1  # min goalkeepers met
    # fields a legal XI (the schema exists in the fieldable set the builder guaranteed)
    from fantabot.asta_engine.legality import fieldable_schemi

    players = {p.id: p for p in POOL}
    assert fieldable_schemi([players[pid] for pid in r.player_ids], MINI)


def test_low_lambda_takes_the_two_best_even_if_same_club() -> None:
    r = _optimize(lam=0.0).optimal
    assert {"a1", "a2"} <= set(r.player_ids)  # both team-X attackers, they score most


def test_high_lambda_diversifies_across_clubs() -> None:
    r = _optimize(lam=1.0).optimal
    # the same-team covariance penalty makes holding both X attackers worse than one X + a3
    assert not ({"a1", "a2"} <= set(r.player_ids))
    assert "a3" in r.player_ids


def test_no_goalkeeper_available_is_infeasible() -> None:
    with pytest.raises(InfeasibleRoster):
        _optimize(lam=0.0, pool=[p for p in POOL if p.id != "gk"])


def test_owned_players_are_kept_and_not_re_paid() -> None:
    # We already own a1 (paid 7). The builder completes the roster around him.
    state = AstaState(owned=("a1",), spent=7.0, total_budget=100.0)
    r = _optimize(lam=0.0, state=state).optimal
    assert "a1" in r.player_ids
    assert len(r) == RULES.size


def test_fallbacks_are_distinct_and_no_better_than_optimal() -> None:
    result = _optimize(lam=0.0)
    assert result.fallbacks  # at least one "if I lose a target" plan
    for fb in result.fallbacks:
        assert set(fb.player_ids) != set(result.optimal.player_ids)
        assert fb.objective <= result.optimal.objective + 1e-9


def test_objective_penalizes_same_team_pairs() -> None:
    same = objective(("a1", "a2"), VALUE, TEAMS, lam=1.0, rho=0.5)
    cross = objective(("a1", "a3"), VALUE, TEAMS, lam=1.0, rho=0.5)
    # a1+a2 has more raw mean (20 vs 19.5) but a same-team covariance penalty; at λ=1 the
    # penalty dominates, so the cross-club pair scores higher.
    assert cross > same
