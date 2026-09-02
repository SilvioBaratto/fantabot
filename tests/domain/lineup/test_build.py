"""`build` — max-weight assignment per module, argmax module, cross-checked against legality.

The builder's job is an exact optimum (Hungarian) and a positionally-legal `starts[]`. Two
things are pinned: it maximises `sum(value)` within and across modules, and its output is
independently confirmed fieldable by `domain/asta/legality` — the check that would have
caught the live `LUP009`.
"""

from __future__ import annotations

import pytest

from fantabot.domain.asta.legality import build_legality, fieldable_schemi, load_compat
from fantabot.domain.asta.roles import MantraPlayer
from fantabot.domain.lineup.build import best_lineup, lineup_for_module
from fantabot.domain.lineup.errors import NoFieldableModule
from fantabot.domain.lineup.models import RosterPlayer

MODULES = ["3412", "3421", "343", "3511", "352", "4141", "4231", "4312", "433", "4411", "442"]


def _p(pid: int, fvmma: float, *roles: str) -> RosterPlayer:
    return RosterPlayer(id=pid, roles=frozenset(roles), fvmma=fvmma)


# A roster whose natural roles field only 3-4-3: no T, no Ds/Dd, one pure striker.
GOLDEN = [
    _p(6482, 6.0, "POR"),
    _p(2788, 8.0, "DC"),
    _p(7564, 7.0, "DC"),
    _p(7274, 6.0, "DC"),
    _p(7181, 7.0, "E"),
    _p(1850, 6.0, "M"),
    _p(5504, 6.0, "C"),
    _p(5678, 5.0, "E"),
    _p(4179, 10.0, "W"),
    _p(6875, 9.0, "A"),
    _p(2194, 5.0, "W"),
]
GOLDEN_VALUE = {p.id: p.fvmma for p in GOLDEN}


def test_picks_the_only_fieldable_module_and_starts_with_the_keeper() -> None:
    module, starts = best_lineup(GOLDEN, MODULES, value=GOLDEN_VALUE)

    assert module == "343"
    assert set(starts) == {p.id for p in GOLDEN}
    assert starts[0] == 6482  # GK is starts[0]


def test_maximises_score_within_a_module_benching_the_weaker_same_role_player() -> None:
    weak_dc = _p(9999, 1.0, "DC")
    roster = [*GOLDEN, weak_dc]
    value = {**GOLDEN_VALUE, 9999: 1.0}

    module, starts = best_lineup(roster, MODULES, value=value)

    assert module == "343"
    assert 9999 not in starts  # the three stronger DCs are preferred


def test_a_roster_with_no_keeper_fields_no_module() -> None:
    outfield_only = [p for p in GOLDEN if "POR" not in p.roles]

    with pytest.raises(NoFieldableModule):
        best_lineup(outfield_only, MODULES, value=GOLDEN_VALUE)


# A roster of universal outfielders + two keepers can field every module.
_UNIVERSAL = ("DC", "DS", "DD", "B", "E", "M", "C", "W", "T", "A", "PC")
UNIVERSAL = [_p(1, 5.0, "POR"), _p(2, 5.0, "POR")] + [
    _p(100 + i, float(i + 1), *_UNIVERSAL) for i in range(13)
]
UNIVERSAL_VALUE = {p.id: p.fvmma for p in UNIVERSAL}
_ROLES_BY_ID = {p.id: p.roles for p in UNIVERSAL}
_LEGALITY = build_legality(load_compat())


@pytest.mark.parametrize("code", MODULES)
def test_the_built_starts_are_confirmed_fieldable_by_legality(code: str) -> None:
    starts = lineup_for_module(UNIVERSAL, code, value=UNIVERSAL_VALUE)

    assert starts is not None and len(starts) == 11
    pool = [MantraPlayer(id=str(pid), roles=_ROLES_BY_ID[pid]) for pid in starts]
    nome = "-".join(code)
    assert nome in fieldable_schemi(pool, _LEGALITY)
