"""`roles_from_marle` — the lineup's numeric role codes to canonical Mantra letters.

`teamLineup.lineUpInfo` carries each player's role as `marle` integers, not letters. The
mapping was derived 2026-09-02 by pairing the codes against roster players whose letter
roles were known (6=Por, 7=Dd, 8=Ds, 9=Dc, 10=E, 11=M, 12=C, 13=T, 14=W, 15=A, 16=Pc). An
unmapped code fails closed rather than guessing a role and building a lineup the platform
rejects.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.errors import UnknownMarleRole
from fantabot.domain.lineup.marle import roles_from_marle


def test_a_single_keeper_code_maps_to_por() -> None:
    assert roles_from_marle([6]) == ["Por"]


def test_a_multi_role_defender_maps_in_order() -> None:
    assert roles_from_marle([7, 9]) == ["Dd", "Dc"]  # Dd/Dc


def test_the_full_derived_range_maps() -> None:
    assert roles_from_marle(list(range(6, 17))) == [
        "Por", "Dd", "Ds", "Dc", "E", "M", "C", "T", "W", "A", "Pc",
    ]


def test_an_unmapped_code_fails_closed_by_number() -> None:
    with pytest.raises(UnknownMarleRole, match="99"):
        roles_from_marle([99])
