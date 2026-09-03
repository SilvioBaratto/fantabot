"""Classic formations and count-based legality — the seven modules and fieldability."""

from __future__ import annotations

import pytest

from fantabot.domain.classic.formations import (
    FORMATION_CODES,
    FORMATIONS,
    classic_fieldable,
    fieldable_formations,
    formation_counts,
)

FULL_ROSTER = {"P": 3, "D": 8, "C": 8, "A": 6}  # the confirmed Classic band [3,8,8,6]


def test_the_seven_confirmed_modules() -> None:
    assert FORMATION_CODES == ("343", "352", "433", "442", "451", "532", "541")
    assert set(FORMATIONS) == set(FORMATION_CODES)


def test_the_digits_are_d_c_a_over_one_keeper() -> None:
    assert FORMATIONS["352"] == {"P": 1, "D": 3, "C": 5, "A": 2}
    assert FORMATIONS["343"] == {"P": 1, "D": 3, "C": 4, "A": 3}
    assert FORMATIONS["541"] == {"P": 1, "D": 5, "C": 4, "A": 1}
    for need in FORMATIONS.values():
        assert need["P"] == 1
        assert need["D"] + need["C"] + need["A"] == 10  # a starting XI is 1 keeper + 10


def test_formation_counts_is_fail_closed() -> None:
    assert formation_counts("442") == {"P": 1, "D": 4, "C": 4, "A": 2}
    with pytest.raises(ValueError):
        formation_counts("4321")  # a Mantra schema code, not a Classic module


def test_a_full_band_fields_every_module() -> None:
    assert fieldable_formations(FULL_ROSTER) == frozenset(FORMATION_CODES)
    assert classic_fieldable(FULL_ROSTER)


def test_a_short_defence_fields_nothing() -> None:
    # every module needs D >= 3, so two defenders field none of them.
    assert fieldable_formations({"P": 1, "D": 2, "C": 8, "A": 6}) == frozenset()
    assert not classic_fieldable({"P": 1, "D": 2, "C": 8, "A": 6})


def test_fieldability_is_a_per_bucket_floor() -> None:
    # exactly 4-5-1 of movement fields only 451: 541 needs D5, the others need C or A more.
    assert fieldable_formations({"P": 1, "D": 4, "C": 5, "A": 1}) == frozenset({"451"})
