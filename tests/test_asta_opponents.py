"""Opponent reconstruction + the advisory surface. Pure and synchronous.

From the buyer-named sale feed we rebuild each rival's roster, spend and role concentration
live; the render turns that plus our target roster and walk-aways into the on-screen frame.
"""

from __future__ import annotations

from fantabot.domain.asta.live import AssignmentEvent
from fantabot.domain.asta.opponents import (
    OpponentState,
    format_advisory,
    format_opponents,
    track_opponents,
)
from fantabot.domain.asta.state import OptimizationResult, Roster

ROLES = {"a1": ("A",), "d1": ("DC", "B"), "p1": ("POR",), "a2": ("A", "W")}
EVENTS = [
    AssignmentEvent("a1", 30, "rival1"),
    AssignmentEvent("d1", 12, "rival1"),
    AssignmentEvent("p1", 5, "rival2"),
    AssignmentEvent("a2", 20, "me"),  # ours — excluded from opponents
    AssignmentEvent("x9", 1, None),  # unnamed buyer — excluded
]


def test_track_opponents_rebuilds_roster_spend_and_roles() -> None:
    opponents = track_opponents(EVENTS, our_team_id="me", roles_by_id=ROLES)
    assert set(opponents) == {"rival1", "rival2"}  # not "me", not the unnamed buyer
    r1 = opponents["rival1"]
    assert r1.players == ("a1", "d1")
    assert r1.spent == 42
    assert r1.role_counts["A"] == 1 and r1.role_counts["DC"] == 1 and r1.role_counts["B"] == 1
    assert r1.remaining(500) == 458


def test_format_opponents_names_the_rivals_and_their_spend() -> None:
    opponents = {"rival1": OpponentState("rival1", ("a1", "d1"), 42, {"A": 1, "DC": 1})}
    text = format_opponents(opponents, names={"rival1": "Team Rossi"}, total_budget=500)
    assert "Team Rossi" in text
    assert "42" in text


def test_format_advisory_lists_targets_by_walkaway() -> None:
    result = OptimizationResult(Roster(("a1", "a2"), total_cost=50.0, objective=99.0))
    text = format_advisory(result, {"a1": 40.0, "a2": 8.0}, names={"a1": "Malen", "a2": "Zaccagni"})
    assert "Malen" in text and "Zaccagni" in text
    # highest walk-away first
    assert text.index("Malen") < text.index("Zaccagni")
