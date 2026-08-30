"""The code-to-name mapping, and the reason it refuses rather than guesses.

Moved from ``tests/test_importers.py`` when the mapping moved out of the seed
package. It never read a file — it takes two iterables — so it outlives the
CSVs unchanged, fed from ``quotazioni.squadra`` and ``voti.squadra_raw``.

The failure it guards against is the quiet one: a partial mapping leaves
``nome_completo`` NULL, later joins drop those rows, and every table still looks
populated.
"""

from __future__ import annotations

import pytest

from fantabot.domain.shared.club_names import TeamMappingError, build_mapping, code_for


class TestTeamMappingIsFailClosed:
    """A partial mapping is worse than no import: a NULL nome_completo makes
    later joins return zero rows while every table still looks populated."""

    def test_the_happy_path_maps_every_code(self) -> None:
        mapping = build_mapping(["Fiorentina", "Milan", "Atalanta"], ["FIO", "MIL", "ATA"])

        assert mapping == {"FIO": "Fiorentina", "MIL": "Milan", "ATA": "Atalanta"}

    def test_a_prefix_collision_raises_instead_of_picking_one(self) -> None:
        with pytest.raises(TeamMappingError, match="not unique"):
            build_mapping(["Milan", "Milano"], ["MIL"])

    def test_a_code_with_no_full_name_raises(self) -> None:
        with pytest.raises(TeamMappingError, match="no full name"):
            build_mapping(["Fiorentina"], ["FIO", "XYZ"])

    def test_the_error_names_the_unresolved_codes(self) -> None:
        with pytest.raises(TeamMappingError) as excinfo:
            build_mapping(["Fiorentina"], ["FIO", "ABC", "DEF"])

        assert "ABC" in str(excinfo.value)
        assert "DEF" in str(excinfo.value)

    def test_a_full_name_with_no_code_is_allowed(self) -> None:
        """Relegated clubs still appear in older voti rows. Extra names are
        harmless; missing ones are not."""
        assert build_mapping(["Fiorentina", "Salernitana"], ["FIO"]) == {
            "FIO": "Fiorentina",
            "SAL": "Salernitana",
        }

    def test_code_for_upper_cases_the_first_three_letters(self) -> None:
        assert code_for("Fiorentina") == "FIO"
        assert code_for(" udinese ") == "UDI"
