"""Edge cases for the asta engine: malformed input and infeasible builds. Pure/sync.

These harden the pieces a live evening will actually stress — a garbled replay line, a
budget that cannot field a rosa, a pool too thin to complete one — where a crash or an
illegal roster would be worse than a clean refusal.
"""

from __future__ import annotations

import pytest

from fantabot.asta_engine.legality import SchemaLegality, SlotRule
from fantabot.asta_engine.live import normalize, parse_assignment
from fantabot.asta_engine.optimizer import InfeasibleRoster, optimize_roster
from fantabot.asta_engine.reservation import reservations
from fantabot.asta_engine.roles import MantraPlayer, normalize_roles
from fantabot.asta_engine.state import AstaState, RosterRules
from fantabot.asta_engine.value import NaiveValueModel

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
POOL = [
    MantraPlayer("a1", normalize_roles(["A"])),
    MantraPlayer("a2", normalize_roles(["A"])),
    MantraPlayer("gk", normalize_roles(["POR"])),
]
TEAMS = {"a1": "X", "a2": "Y", "gk": "Z"}
PRICES = {"a1": 1.0, "a2": 1.0, "gk": 1.0}
VALUE = NaiveValueModel(signals={"a1": 9.0, "a2": 8.0, "gk": 3.0}, prior_mean=1.0, base_variance=4.0, no_history_variance=4.0)


def _kw() -> dict[str, object]:
    return dict(value=VALUE, prices=PRICES, teams=TEAMS, legality=MINI, rules=RULES, lam=0.0)


# --- malformed live input ---------------------------------------------------------------


def test_parse_assignment_tolerates_a_non_mapping_state() -> None:
    # A garbled replay line must not crash the live loop.
    assert parse_assignment(["not", "a", "dict"]) is None  # type: ignore[arg-type]
    assert parse_assignment("garbage") is None  # type: ignore[arg-type]


def test_normalize_skips_garbage_and_keeps_sales() -> None:
    states = [
        "junk",
        {"update_type": "raise", "player_id": "1", "price": 5},
        {"update_type": "close_auction", "player_id": "1", "price": 30, "fantateam_id": "a"},
    ]
    events = normalize(states)  # type: ignore[arg-type]
    assert [e.player_id for e in events] == ["1"]


# --- infeasible builds ------------------------------------------------------------------


def test_a_budget_too_small_to_field_a_rosa_is_refused() -> None:
    with pytest.raises(InfeasibleRoster):
        optimize_roster(AstaState(total_budget=2.0), POOL, **_kw())  # type: ignore[arg-type]


def test_a_pool_too_thin_to_complete_a_rosa_is_refused() -> None:
    thin = [MantraPlayer("a1", normalize_roles(["A"])), MantraPlayer("gk", normalize_roles(["POR"]))]
    with pytest.raises(InfeasibleRoster):
        optimize_roster(AstaState(total_budget=100.0), thin, **_kw())  # type: ignore[arg-type]


# --- determinism + reservations bound ---------------------------------------------------


def test_the_optimizer_is_deterministic() -> None:
    a = optimize_roster(AstaState(total_budget=100.0), POOL, **_kw())  # type: ignore[arg-type]
    b = optimize_roster(AstaState(total_budget=100.0), POOL, **_kw())  # type: ignore[arg-type]
    assert a.optimal.player_ids == b.optimal.player_ids
    assert a.optimal.objective == b.optimal.objective


def test_reservations_with_no_targets_is_empty() -> None:
    _, walkaways = reservations(AstaState(total_budget=100.0), POOL, n_targets=0, **_kw())  # type: ignore[arg-type]
    assert walkaways == {}
