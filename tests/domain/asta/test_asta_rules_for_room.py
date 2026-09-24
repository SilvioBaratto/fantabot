"""`RosterRules`, derived from what a room actually declares, with a stated provenance.

Reading the room too literally is the named risk (`tasks/archive/parity-plan.md` §2): a room under
`"no-limit-per-role"` has no per-role floor to read at all, and the room's own
`min_player`/`max_player` totals say only "at least this many players" — never how many of
them must be goalkeepers. Deriving a zero-keeper floor from that silence would be a
room-declared rule no room actually stated.

Which is why, since 2026-09-24, the two totals are not parameters: they were passed by both
call sites and read by neither, so the signature claimed a reading of the room that the body
never made. `TestARoomThatDeclaresNothingUsable` below carries what is left of that
property, and why a value can no longer pin it.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from fantabot.domain.asta.state import (
    ASSUMED_NOTHING,
    ROOM_DECLARED,
    RosterRules,
    rules_for_room,
)
from fantabot.domain.classic.state import ClassicRosterRules


class TestAClassicStaticRoom:
    """A Classic room declares `number_of_players_selection == "static"` and a
    `players_settings_data` per-role band — a four-role floor Mantra's two-super-role shape
    cannot express. Confirmed live 3584692 (docs/classic/task0-capture.md)."""

    def test_static_selection_builds_a_classic_band(self) -> None:
        rules, provenance = rules_for_room(
            selection="static", classic_band={"P": 3, "D": 8, "C": 8, "A": 6},
        )
        assert isinstance(rules, ClassicRosterRules)
        assert rules.size == 25
        assert rules.min_of("D") == 8
        assert rules.max_of("A") == 6
        assert provenance == ROOM_DECLARED

    def test_static_without_a_band_falls_back_to_assumed(self) -> None:
        rules, provenance = rules_for_room(selection="static")
        assert isinstance(rules, RosterRules)  # nothing to read -> the honest assumed default
        assert provenance == ASSUMED_NOTHING


class TestARoomThatDeclaresTheBand:
    def test_the_band_is_read_and_labelled(self) -> None:
        rules, provenance = rules_for_room(
            selection="min-max-goalie-others", min_goalkeepers=2, min_others=23,
        )

        assert rules == RosterRules(size=25, min_goalkeepers=2, min_movement=23)
        assert provenance == ROOM_DECLARED

    def test_target_size_overrides_the_bands_own_sum(self) -> None:
        """An operator's explicit choice — target `max_player`, say — not this function's."""
        rules, provenance = rules_for_room(
            selection="min-max-goalie-others", min_goalkeepers=2, min_others=23, target_size=30,
        )

        assert rules.size == 30
        assert provenance == ROOM_DECLARED


class TestARoomThatDeclaresNothingUsable:
    def test_no_selection_at_all_is_assumed(self) -> None:
        rules, provenance = rules_for_room(selection=None)

        assert rules == RosterRules()
        assert provenance == ASSUMED_NOTHING

    def test_no_limit_per_role_has_no_band_to_read(self) -> None:
        """The common case — 153 of 247 rooms in the live registry — and it declares a
        total, never a split."""
        rules, provenance = rules_for_room(selection="no-limit-per-role")

        assert rules == RosterRules()
        assert provenance == ASSUMED_NOTHING

    def test_a_rooms_totals_have_no_way_in_at_all(self) -> None:
        """The named risk, pinned where it is still decidable: **a stated total must not
        manufacture a keeper floor no room declared.**

        This test used to pass `min_player=25, max_player=30` and assert the default came
        back, and it was vacuous — `rules_for_room` took both totals and branched on
        neither, so it passed on the `selection` arm alone and would have passed
        identically with `min_player=None`. The reading it was written to forbid could only
        have been written into a branch this call never reaches.

        With the parameters gone the property is no longer about a value, so no value can
        pin it: what is left is that there is no input by which a room's totals could reach
        the band. The signature is the strongest statement of that, and it goes red the
        moment either one is wired back in.
        """
        parameters = inspect.signature(rules_for_room).parameters

        assert "min_player" not in parameters
        assert "max_player" not in parameters
        assert {"selection", "min_goalkeepers", "min_others"} <= set(parameters), (
            "the fields it does read must still be readable"
        )

    def test_the_right_selection_with_only_half_the_band_is_still_assumed(self) -> None:
        rules, provenance = rules_for_room(
            selection="min-max-goalie-others", min_goalkeepers=2, min_others=None,
        )

        assert rules == RosterRules()
        assert provenance == ASSUMED_NOTHING

    def test_the_default_is_the_platforms_universal_mantra_floor_not_a_zero(self) -> None:
        """Silence is "unknown," not "no goalkeepers required" — the platform's own Mantra
        rule is a minimum of 2 regardless of what any single league says."""
        rules, _ = rules_for_room(selection=None)

        assert rules.min_goalkeepers == 2


class TestTheRegistryRegression:
    """Measured over the live registry: no Mantra room declares `min_player == 30`. The old
    hard-coded default was never "what rooms actually say" — it was one league's own setting.
    """

    @staticmethod
    def _mantra_min_players() -> list[int | None]:
        fixture = Path(__file__).parents[2] / "golden" / "seed_live_sample.json"
        rows = json.loads(fixture.read_text(encoding="utf-8"))
        # SEED_FIELDS order (domain/harvest/registry.py): min_player is index 4,
        # asta_type is index 11.
        return [row[4] for row in rows if row[11] == "mantra"]

    def test_no_mantra_room_in_the_registry_declares_thirty(self) -> None:
        min_players = self._mantra_min_players()

        assert min_players, "the fixture must actually carry Mantra rows"
        assert 30 not in min_players

    def test_the_fixture_is_not_all_nulls(self) -> None:
        """A fixture where every room is silent would pass the assertion above for the wrong
        reason — this proves real, non-null values are actually represented."""
        min_players = self._mantra_min_players()

        assert any(mp is not None for mp in min_players)
