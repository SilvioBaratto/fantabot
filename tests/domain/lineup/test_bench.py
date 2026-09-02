"""`order_bench` — the reserves, GK first, then by score. Pure.

The platform's first bench slot is the goalkeeper's; the rest drive the automatic-
substitution order, so they are ranked by the same value as the XI. Fail-closed when there
is no reserve keeper or too few players to fill the bench.
"""

from __future__ import annotations

import pytest

from fantabot.domain.lineup.bench import order_bench
from fantabot.domain.lineup.errors import BenchIncomplete
from fantabot.domain.lineup.models import RosterPlayer


def _p(pid: int, fvmma: float, *roles: str) -> RosterPlayer:
    return RosterPlayer(id=pid, roles=frozenset(roles), fvmma=fvmma)


STARTS = list(range(1, 12))  # 11 started ids, 1..11
RESERVE_GK = _p(50, 6.0, "POR")
RESERVES = [RESERVE_GK] + [_p(60 + i, float(i), "DC", "M", "A") for i in range(12)]  # ids 60..71
ROSTER = [_p(pid, 9.0, "POR") for pid in STARTS] + RESERVES
VALUE = {p.id: p.fvmma for p in ROSTER}


def test_bench_slot_zero_is_a_reserve_keeper() -> None:
    bench = order_bench(ROSTER, STARTS, value=VALUE, size=12)

    assert bench[0] == 50


def test_bench_is_sized_and_disjoint_from_the_starters() -> None:
    bench = order_bench(ROSTER, STARTS, value=VALUE, size=12)

    assert len(bench) == 12
    assert set(bench).isdisjoint(STARTS)


def test_the_outfield_reserves_are_ordered_by_score_descending() -> None:
    bench = order_bench(ROSTER, STARTS, value=VALUE, size=12)

    outfield = bench[1:]
    scores = [VALUE[pid] for pid in outfield]
    assert scores == sorted(scores, reverse=True)
    # 13 reserves compete for 11 outfield slots -> the two lowest (ids 60, 61 at 0.0, 1.0) drop
    assert 60 not in bench


def test_no_reserve_keeper_fails_closed() -> None:
    roster = [p for p in ROSTER if p.id != 50]

    with pytest.raises(BenchIncomplete):
        order_bench(roster, STARTS, value=VALUE, size=12)


def test_too_few_reserves_to_fill_the_bench_fails_closed() -> None:
    roster = [_p(pid, 9.0, "POR") for pid in STARTS] + [RESERVE_GK, _p(60, 1.0, "DC")]

    with pytest.raises(BenchIncomplete):
        order_bench(roster, STARTS, value=VALUE, size=12)
