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



# -- the CLI that fills `names` (interface/asta.py) --------------------------------------
#
# `format_opponents` is tested above with a real mapping, and passed with one for a year —
# while its only production caller passed the literal `{}`. A pure-function test cannot see
# that: the argument is the seam, so the seam is what these drive.

import contextlib  # noqa: E402 — grouped with the tests that need them
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from typer.testing import CliRunner  # noqa: E402


class _Seat:
    """`rest.Seat`, down to the two fields this path reads."""

    def __init__(self, fantateam_id: str, team_name: str | None) -> None:
        self.fantateam_id = fantateam_id
        self.team_name = team_name


def _room(*seats: _Seat) -> Any:
    """A `RoomConfig` as `_declared_room` returns one: the format, the shape and the seats."""
    return type(
        "_Room",
        (),
        {"asta_type": "mantra", "num_teams": 8, "num_credits": 500, "seats": seats},
    )()


def _run_asta_live(
    monkeypatch: pytest.MonkeyPatch, *, room: Any, rival_id: str = "rival-uuid-1"
) -> str:
    """`asta live --league`, with the probe answering `room` and the fold faked.

    Two things are real and they are the two under test: what the Typer body decides to pass
    as `names`, and `format_opponents` itself. `world.names` deliberately holds a player the
    advisory renders by name, so a body reaching for the wrong mapping shows up as a name in
    the wrong block rather than as an exception.
    """
    from fantabot.adapters.http.fantalab import feed, listone
    from fantabot.adapters.persistence import database_manager
    from fantabot.application import asta_advisory
    from fantabot.interface import asta
    from fantabot.interface.app import app

    class _World:
        names = {"a1": "Malen"}  # noqa: RUF012 - a stub, not a shared mutable default

    class _Advisory:
        dropped_sales = 0
        result = OptimizationResult(Roster(("a1",), total_cost=30.0, objective=9.0))
        walkaways = {"a1": 30.0}  # noqa: RUF012
        rivals = {rival_id: OpponentState(rival_id, ("a1",), 42, {"A": 1})}  # noqa: RUF012
        world = _World()

    monkeypatch.setattr(asta, "_declared_room", lambda *_a, **_k: room)
    monkeypatch.setattr(asta, "_recorded_format", lambda _fl: "mantra")
    monkeypatch.setattr(listone, "fetch", lambda **_k: {"uuid-1": 1})
    monkeypatch.setattr(feed, "ledger_events", lambda *_a, **_k: [])
    monkeypatch.setattr(
        database_manager, "get_session", lambda: contextlib.nullcontext(object())
    )
    monkeypatch.setattr(asta_advisory, "build_advisory", lambda *_a, **_k: _Advisory())

    result = CliRunner().invoke(
        app, ["asta", "live", "--league", "FL1", "--db", "1", "--team", "us"]
    )
    assert result.exit_code == 0, result.output
    return result.output


def _opponents_block(output: str) -> str:
    assert "opponents:" in output, output
    return output[output.index("opponents:") :]


class TestTheOpponentsColumnCanShowAName:
    """`asta live` called `format_opponents(..., names={})` — an empty dict *literal*.

    `names.get(team_id, team_id)` therefore fell back on every row of every run, so the
    column was structurally unable to print anything but a fantateam uuid. The mapping was
    one attribute away: the same `POST /fantaleague/fetch` that already answers the format
    and the corpus shape carries the seat list, and `asta room` has always built
    `RoomRules.team_names` from exactly that.
    """

    def test_a_declared_seat_is_shown_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        block = _opponents_block(
            _run_asta_live(
                monkeypatch, room=_room(_Seat("rival-uuid-1", "Team Rossi"), _Seat("us", "Noi"))
            )
        )

        assert "Team Rossi" in block
        assert "rival-uuid-1" not in block

    def test_a_seat_with_no_name_falls_back_to_its_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`team_name` is `str | None` on `rest.Seat`, and `None` must not reach the column
        as the word "None" — the same `or` `asta_room` uses."""
        block = _opponents_block(
            _run_asta_live(monkeypatch, room=_room(_Seat("rival-uuid-1", None)))
        )

        assert "rival-uuid-1" in block
        assert "None" not in block

    def test_a_room_that_could_not_be_asked_still_shows_the_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The probe degrades to `None` by design (`_declared_room` is never fatal), and
        `--replay` has no seat list at all — which is what keeps its goldens byte-identical.

        Without this half, a body that hardcoded one rival's name would pass the first test.
        """
        assert "rival-uuid-1" in _opponents_block(_run_asta_live(monkeypatch, room=None))

    def test_the_player_name_map_is_not_what_gets_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`advisory.world.names` is the nearest mapping in scope and is the wrong one.

        It is keyed by **player** id (`plan_inputs.py:126`, `{pid: row.nome}`) and is what
        the roster table one line up renders with; these keys are fantateam ids. Handing it
        over would fall back for every rival just as `{}` did, *and* would print a player's
        name for any id that collided — so here the rival's id is one the advisory knows as
        a player, and the claim is that the opponents block does not call him that.
        """
        output = _run_asta_live(monkeypatch, room=None, rival_id="a1")

        assert "Malen" in output, "precondition: the roster table renders him by name"
        assert "Malen" not in _opponents_block(output)
