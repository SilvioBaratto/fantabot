"""Classic lineup: formation slots as single-role count-slots, and the max-value XI over them."""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.build import best_lineup
from fantabot.domain.lineup.models import RosterPlayer
from fantabot.domain.lineup.schema import classic_slots


def _p(pid: int, role: str, val: float) -> RosterPlayer:
    return RosterPlayer(id=pid, roles=frozenset({role}), fvmma=val)


def test_classic_slots_expand_a_formation_gk_first() -> None:
    s = classic_slots("352")
    assert s == (
        frozenset({"P"}),
        *([frozenset({"D"})] * 3),
        *([frozenset({"C"})] * 5),
        *([frozenset({"A"})] * 2),
    )
    assert len(s) == 11


def test_an_unknown_module_raises() -> None:
    with pytest.raises(ValueError):
        classic_slots("4321")  # a Mantra schema code, not a Classic formation


def test_best_classic_xi_takes_the_top_value_per_bucket() -> None:
    # more than enough per role; the matcher must take the highest-value in each bucket.
    roster = [
        _p(1, "P", 5), _p(2, "P", 3),
        _p(10, "D", 9), _p(11, "D", 8), _p(12, "D", 7), _p(13, "D", 1),
        _p(20, "C", 9), _p(21, "C", 8), _p(22, "C", 7), _p(23, "C", 6), _p(24, "C", 5), _p(25, "C", 1),
        _p(30, "A", 9), _p(31, "A", 8), _p(32, "A", 1),
    ]
    value = {p.id: p.fvmma for p in roster}
    module, starts = best_lineup(roster, ["352"], value=value, slots_provider=classic_slots)

    assert module == "352"
    assert set(starts) == {1, 10, 11, 12, 20, 21, 22, 23, 24, 30, 31}  # top per bucket
    assert starts[0] == 1  # GK first


def test_a_short_bucket_makes_the_module_unfieldable() -> None:
    roster = [_p(1, "P", 5), _p(10, "D", 9), _p(20, "C", 9), _p(30, "A", 9)]  # far too few
    from fantabot.domain.lineup.errors import NoFieldableModule

    with pytest.raises(NoFieldableModule):
        best_lineup(roster, ["352"], value={p.id: p.fvmma for p in roster}, slots_provider=classic_slots)
