"""Opponent reconstruction + the advisory surface. Pure and synchronous.

From the buyer-named sale feed we rebuild each rival's roster, spend and role concentration
live; the render turns that plus our target roster and walk-aways into the on-screen frame.
"""

from __future__ import annotations

from typing import ClassVar

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


class TestATargetWeWouldNeverBidOnIsNotCalledAChase:
    """`chase X walk-away 0` reads as an instruction. It is the opposite of one.

    `reservations` clamps a negative walk-away to 0 — the greedy builder is a heuristic,
    and a roster without a target can score higher, which just means he is freely
    replaceable. The bidder agrees: its smallest possible raise is `current + step`, so a
    ceiling of 0 refuses at every price, with the reason `walk_away`. The advisory was the
    only part of the system calling that a chase.

    The line stays — he is in the target roster and the operator should see him — but it
    says what the bidder will actually do, in the bidder's own word.
    """

    RESULT = OptimizationResult(
        optimal=Roster(player_ids=("a1", "a2", "a3"), total_cost=48, objective=100.0),
        fallbacks=(),
    )
    NAMES: ClassVar[dict[str, str]] = {"a1": "Malen", "a2": "Zaccagni", "a3": "Vasquez"}

    def _lines(self) -> list[str]:
        walkaways = {"a1": 40.0, "a2": 8.0, "a3": 0.0}
        return format_advisory(self.RESULT, walkaways, self.NAMES).splitlines()[1:]

    def test_a_payable_ceiling_is_still_a_chase(self) -> None:
        assert self._lines()[0].startswith("  chase Malen")
        assert self._lines()[1].startswith("  chase Zaccagni")

    def test_a_zero_ceiling_says_pass_not_chase(self) -> None:
        """`pass` is the bidder's own word for it — `room.run_bid_loop` logs
        `pass on <target>: walk_away` when this exact ceiling refuses."""
        line = self._lines()[2]

        assert "chase" not in line
        assert line.startswith("  pass  Vasquez")

    def test_the_reason_is_on_the_line_so_it_needs_no_explaining(self) -> None:
        assert "freely replaceable" in self._lines()[2]

    def test_he_is_still_listed_because_he_is_in_the_target_roster(self) -> None:
        assert "Vasquez" in "\n".join(self._lines())
        assert len(self._lines()) == 3
