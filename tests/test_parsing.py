"""The two decimal conventions, and the two splitters. No database anywhere.

Moved verbatim from ``tests/test_importers.py`` when the parsers moved out of
``db/importers/_csv.py``: the rules they pin are facts about how the site renders
numbers, not about how a CSV was stored, so they outlived the importers — that
package was retired on 2026-08-30 and these rules did not move again.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from fantabot.parsing import (
    italian_decimal,
    parse_date,
    parse_time,
    plain_decimal,
    split_codes,
    split_flags,
)


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
    """These run at scrape time now, on the hot path of every write, so they
    must stay pure — and their tests never need the db marker."""
    from fantabot import parsing as module

    assert module.__file__ is not None
    text = Path(module.__file__).read_text()
    assert "sqlalchemy" not in text
    assert "fantabot.db" not in text


class TestSplitFlags:
    def test_case_is_preserved_unlike_role_codes(self) -> None:
        assert split_flags("floor_qi;team_discount(MIL)") == [
            "floor_qi",
            "team_discount(MIL)",
        ]
        assert split_codes("floor_qi") == ["FLOOR_QI"]

    def test_empty_is_an_empty_list(self) -> None:
        assert split_flags("") == []


class TestMatchGrainParsing:
    def test_dates_are_read_in_italian_order(self) -> None:
        """01/02/2025 is 1 February, not 2 January. Read the American way,
        every match in the first twelve days of a month lands in the wrong one."""
        assert parse_date("01/02/2025") == date(2025, 2, 1)

    def test_a_kick_off_time_is_parsed(self) -> None:
        assert parse_time("12:30") == time(12, 30)

    def test_a_missing_kick_off_time_is_none(self) -> None:
        """bonus_malus carries no time at all; voti carries one for every row."""
        assert parse_time("") is None
        assert parse_time("   ") is None
