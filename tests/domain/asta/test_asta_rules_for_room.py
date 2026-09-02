"""`RosterRules`, derived from what a room actually declares, with a stated provenance.

Reading the room too literally is the named risk (`tasks/plan.md` §2): a room under
`"no-limit-per-role"` has no per-role floor to read at all, and `min_player`/`max_player`
alone say only "at least this many total" — never how many must be goalkeepers. Deriving a
zero-keeper floor from that silence would be a room-declared rule no room actually stated.
"""

from __future__ import annotations

import json
from pathlib import Path

from fantabot.domain.asta.state import (
    ASSUMED_NOTHING,
    ROOM_DECLARED,
    RosterRules,
    rules_for_room,
)


class TestARoomThatDeclaresTheBand:
    def test_the_band_is_read_and_labelled(self) -> None:
        rules, provenance = rules_for_room(
            selection="min-max-goalie-others", min_player=25, max_player=30,
            min_goalkeepers=2, min_others=23,
        )

        assert rules == RosterRules(size=25, min_goalkeepers=2, min_movement=23)
        assert provenance == ROOM_DECLARED

    def test_target_size_overrides_the_bands_own_sum(self) -> None:
        """An operator's explicit choice — target `max_player`, say — not this function's."""
        rules, provenance = rules_for_room(
            selection="min-max-goalie-others", min_player=25, max_player=30,
            min_goalkeepers=2, min_others=23, target_size=30,
        )

        assert rules.size == 30
        assert provenance == ROOM_DECLARED


class TestARoomThatDeclaresNothingUsable:
    def test_no_selection_at_all_is_assumed(self) -> None:
        rules, provenance = rules_for_room(selection=None, min_player=None, max_player=None)

        assert rules == RosterRules()
        assert provenance == ASSUMED_NOTHING

    def test_no_limit_per_role_has_no_band_to_read_even_with_a_total(self) -> None:
        """The named risk, pinned: `min_player` alone must not manufacture a keeper floor no
        room actually stated."""
        rules, provenance = rules_for_room(
            selection="no-limit-per-role", min_player=25, max_player=30,
        )

        assert rules == RosterRules()
        assert provenance == ASSUMED_NOTHING

    def test_the_right_selection_with_only_half_the_band_is_still_assumed(self) -> None:
        rules, provenance = rules_for_room(
            selection="min-max-goalie-others", min_player=25, max_player=30,
            min_goalkeepers=2, min_others=None,
        )

        assert rules == RosterRules()
        assert provenance == ASSUMED_NOTHING

    def test_the_default_is_the_platforms_universal_mantra_floor_not_a_zero(self) -> None:
        """Silence is "unknown," not "no goalkeepers required" — the platform's own Mantra
        rule is a minimum of 2 regardless of what any single league says."""
        rules, _ = rules_for_room(selection=None, min_player=None, max_player=None)

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
