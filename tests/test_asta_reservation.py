"""Rolling re-optimization and the walk-away (reservation) price. Pure and synchronous.

As each player is sold, ``apply_event`` folds the sale into our state (ours -> owned+spent,
anyone's -> taken) and the roster is re-optimized. The reservation for a target is how much
objective value we lose by not securing him — in the value signal's own (credit-like) units,
capped at the remaining budget; a target whose loss makes the roster infeasible is essential
and reserves the whole budget.
"""

from __future__ import annotations

from fantabot.asta_engine.legality import SchemaLegality, SlotRule
from fantabot.asta_engine.live import AssignmentEvent
from fantabot.asta_engine.reservation import apply_event, reservations, rolling_advisory
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
    MantraPlayer("a3", normalize_roles(["A"])),
    MantraPlayer("gk", normalize_roles(["POR"])),
]
TEAMS = {"a1": "X", "a2": "Y", "a3": "Z", "gk": "W"}
PRICES = {"a1": 10.0, "a2": 10.0, "a3": 9.0, "gk": 5.0}
VALUE = NaiveValueModel(
    signals={"a1": 10.0, "a2": 10.0, "a3": 9.0, "gk": 3.0},
    prior_mean=1.0,
    base_variance=4.0,
    no_history_variance=4.0,
)


def _kw() -> dict[str, object]:
    return dict(value=VALUE, prices=PRICES, teams=TEAMS, legality=MINI, rules=RULES, lam=0.0)


def test_apply_event_folds_our_purchase_into_owned_and_spent() -> None:
    state = AstaState(total_budget=100.0)
    after = apply_event(state, AssignmentEvent("a1", 12, "me"), our_team_id="me")
    assert after.owned == ("a1",)
    assert after.spent == 12.0
    assert "a1" in after.taken


def test_apply_event_marks_a_rivals_purchase_as_taken_only() -> None:
    state = AstaState(total_budget=100.0)
    after = apply_event(state, AssignmentEvent("a1", 12, "rival"), our_team_id="me")
    assert after.owned == ()
    assert after.spent == 0.0
    assert "a1" in after.taken


def test_reservation_is_higher_for_an_essential_player() -> None:
    state = AstaState(total_budget=100.0)
    _, walkaways = reservations(state, POOL, **_kw())  # type: ignore[arg-type]
    # The keeper is the only POR — losing him makes the roster infeasible -> full budget.
    assert walkaways["gk"] == state.remaining_budget
    # A fungible attacker is worth only its small marginal edge over the alternative.
    assert 0 < walkaways["a1"] < state.remaining_budget


def test_rolling_replans_when_a_target_is_taken_by_a_rival() -> None:
    state = AstaState(total_budget=100.0)
    events = [AssignmentEvent("a1", 10, "rival")]  # a rival buys a1
    steps = list(rolling_advisory(state, POOL, events, our_team_id="me", **_kw()))  # type: ignore[arg-type]
    assert len(steps) == 1
    _, _, result, _ = steps[0]
    assert "a1" not in result.optimal.player_ids  # a1 is gone, the plan moved on
