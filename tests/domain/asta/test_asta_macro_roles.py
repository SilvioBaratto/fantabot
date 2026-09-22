"""Macro roles: the coarse buckets a fade is fitted in, and the lineup projection pools by.

They lived in `application/pricing.py`, where `domain/` could not reach them. The lineup
projection (T15) pools players by macro role and is domain code, so the table moved here
and `pricing` re-imports it — one table, not a copy that can drift from it.
"""

from __future__ import annotations

import pytest

from fantabot.application import pricing
from fantabot.domain.asta import roles
from fantabot.domain.asta.roles import GOALKEEPER_MACRO, macro_role


class TestMacroRole:
    """Two role systems, one output vocabulary."""

    def test_classic_maps_its_single_letter(self) -> None:
        assert macro_role("p", "classic") == GOALKEEPER_MACRO
        assert macro_role("d", "classic") == "DEF"
        assert macro_role("c", "classic") == "MID"
        assert macro_role("a", "classic") == "ATT"

    def test_mantra_takes_the_first_component_of_a_compound(self) -> None:
        """`DC;DD` is a defender who also plays right back. The first is his primary."""
        assert macro_role("DC;DD", "mantra") == "DEF"
        assert macro_role("W;A", "mantra") == "MID_ATT"

    def test_mantra_splits_wingers_and_trequartisti_from_midfield(self) -> None:
        """MID_ATT exists because those two fade differently from a `C`."""
        assert macro_role("C", "mantra") == "MID"
        assert macro_role("W", "mantra") == "MID_ATT"
        assert macro_role("T", "mantra") == "MID_ATT"
        assert macro_role("A", "mantra") == "ATT"

    def test_an_unknown_code_raises_rather_than_guessing_a_bucket(self) -> None:
        """A silent default would price a whole role off another role's fade."""
        with pytest.raises(KeyError):
            macro_role("ZZ", "mantra")


@pytest.mark.parametrize("name", ["GOALKEEPER_MACRO", "macro_role"])
def test_pricing_re_imports_the_domain_s_table_rather_than_keeping_its_own(name: str) -> None:
    assert getattr(pricing, name) is getattr(roles, name)
