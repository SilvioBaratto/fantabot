"""Parsing primitives for the ten source CSVs. Pure, no database.

The decimal separator is not consistent across the files, and the difference is
silent rather than loud. Measured on the real data:

    statistiche_*.csv   13222 comma-decimals, 0 dot, 2846 "0,0" sentinels
    voti.csv           102100 comma-decimals, 0 dot
    qi_bias_*.csv           0 comma-decimals, dot only
    target_price_*.csv      0 comma-decimals, dot only, 523 blanks

One shared parser therefore has to guess, and guessing wrong is not an error —
``Decimal("38.46".replace(",", "."))`` is fine, and ``"38,46"`` read as a plain
decimal is ``3846``. Two parsers that each refuse the other's format turn a
silent hundredfold error into a crash on the first row.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from fantabot.db.importers._csv import italian_decimal, plain_decimal, split_codes
from fantabot.db.importers.players import PlayerRef, read_refs, resolve_names


class TestItalianDecimal:
    """statistiche_*.csv and voti.csv."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("6,25", Decimal("6.25")),
            ("-8,6", Decimal("-8.6")),
            (" 6,5 ", Decimal("6.5")),
            ("6", Decimal("6")),
            ("0,5", Decimal("0.5")),
        ],
    )
    def test_parses_comma_decimals(self, raw: str, expected: Decimal) -> None:
        assert italian_decimal(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "0,0"])
    def test_no_data_sentinels_become_none(self, raw: str) -> None:
        """Empty is did-not-play; "0,0" is no-data. Both are absent, not zero —
        SPEC criterion 9 requires 2846 NULLs and no zeros in media_voto."""
        assert italian_decimal(raw) is None

    def test_zero_that_is_really_zero_survives(self) -> None:
        """A genuine 0 (not the "0,0" sentinel) must not be swallowed."""
        assert italian_decimal("0") == Decimal("0")

    def test_a_dot_decimal_raises_instead_of_multiplying_by_a_hundred(self) -> None:
        with pytest.raises(ValueError, match="dot"):
            italian_decimal("38.46")

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError):
            italian_decimal("n/a")


class TestPlainDecimal:
    """qi_bias_*.csv and target_price_*.csv."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("38.46", Decimal("38.46")),
            ("-0.5", Decimal("-0.5")),
            (" 1.25 ", Decimal("1.25")),
            ("7", Decimal("7")),
        ],
    )
    def test_parses_dot_decimals(self, raw: str, expected: Decimal) -> None:
        assert plain_decimal(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_becomes_none(self, raw: str) -> None:
        assert plain_decimal(raw) is None

    def test_zero_point_zero_is_a_value_not_a_sentinel(self) -> None:
        """Unlike the Italian files, these use a blank for no-data, so 0.0 is a
        real measurement — pct_delta can legitimately be zero."""
        assert plain_decimal("0.0") == Decimal("0.0")

    def test_a_comma_decimal_raises_instead_of_guessing(self) -> None:
        with pytest.raises(ValueError, match="comma"):
            plain_decimal("6,25")


class TestSplitCodes:
    """``;``-joined role codes become ``text[]``."""

    def test_splits_and_upper_cases(self) -> None:
        assert split_codes("B;DS;E") == ["B", "DS", "E"]

    def test_normalises_case_and_whitespace(self) -> None:
        """quotazioni_mantra stores uppercase, mantra_schemi.json mixed case,
        and the rules doc lowercase. Something has to normalise."""
        assert split_codes(" b ; Dc ;pc ") == ["B", "DC", "PC"]

    def test_a_single_code_becomes_a_one_element_list(self) -> None:
        """Classic shares the Mantra column, holding one element."""
        assert split_codes("P") == ["P"]

    def test_empty_becomes_an_empty_list_not_a_list_containing_empty(self) -> None:
        assert split_codes("") == []
        assert split_codes(";;") == []


def test_the_parsers_do_not_reach_for_a_database() -> None:
    """This module is imported by every importer and must stay pure, so its
    tests never need the db marker."""
    from fantabot.db.importers import _csv as module

    assert module.__file__ is not None
    text = Path(module.__file__).read_text()
    assert "sqlalchemy" not in text
    assert "fantabot.db.engine" not in text


class TestPlayerNameResolution:
    """94 of the 1474 ids spell their name more than one way across seasons, so
    the seed needs a rule that does not depend on which file was read first."""

    @staticmethod
    def _ref(player_id: int, nome: str, stagione: str, rank: int) -> PlayerRef:
        return PlayerRef(player_id=player_id, nome=nome, stagione=stagione, source_rank=rank)

    def test_the_most_recent_season_wins(self) -> None:
        refs = [
            self._ref(1, "SORIANO", "2022/23", 0),
            self._ref(1, "Soriano", "2026/27", 0),
        ]
        assert resolve_names(refs) == {1: "Soriano"}

    def test_the_result_does_not_depend_on_input_order(self) -> None:
        """A seed that varied with file order would be unreproducible."""
        refs = [
            self._ref(1, "Soriano", "2026/27", 0),
            self._ref(1, "SORIANO", "2022/23", 0),
        ]
        assert resolve_names(refs) == {1: "Soriano"}
        assert resolve_names(list(reversed(refs))) == {1: "Soriano"}

    def test_within_one_season_the_more_canonical_source_wins(self) -> None:
        """quotazioni (rank 0) over voti (rank 2) for the same season."""
        refs = [
            self._ref(1, "From voti", "2026/27", 2),
            self._ref(1, "From quotazioni", "2026/27", 0),
        ]
        assert resolve_names(refs) == {1: "From quotazioni"}

    def test_every_distinct_id_survives(self) -> None:
        refs = [self._ref(i, f"P{i}", "2026/27", 0) for i in range(5)]
        assert len(resolve_names(refs)) == 5

    def test_no_refs_is_an_empty_mapping_not_an_error(self) -> None:
        assert resolve_names([]) == {}


class TestPlayerRefReading:
    def test_rows_without_an_id_are_skipped(self, tmp_path: Path) -> None:
        """3039 coach rows per match-grain file carry an empty id."""
        csv_path = tmp_path / "voti.csv"
        csv_path.write_text(
            "stagione,id,nome\n2026/27,7,Real Player\n2026/27,,Allenatore\n",
            encoding="utf-8",
        )

        refs = list(read_refs(tmp_path))

        assert [ref.player_id for ref in refs] == [7]

    def test_a_missing_source_file_is_not_an_error(self, tmp_path: Path) -> None:
        assert list(read_refs(tmp_path)) == []
