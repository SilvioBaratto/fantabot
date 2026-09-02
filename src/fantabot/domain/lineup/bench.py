"""Order the reserves for the bench. Pure.

The platform's first bench slot is the goalkeeper's; the remaining slots drive the
automatic-substitution engine (`settings/calculate.subst`), so they are ranked by the same
value as the starting XI — best sub first. Fail-closed: a bench with no reserve keeper, or
too few players to fill it, is refused rather than sent and rejected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fantabot.domain.lineup.errors import BenchIncomplete
from fantabot.domain.lineup.models import RosterPlayer

#: The goalkeeper role, required in bench slot 0.
GK_ROLE = "POR"


def order_bench(
    roster: Sequence[RosterPlayer],
    starts: Sequence[int],
    *,
    value: Mapping[int, float],
    size: int,
    gk_role: str = GK_ROLE,
) -> list[int]:
    """`[reserve_keeper, then top-scoring reserves...]`, length `size`, disjoint from `starts`.

    Raises `BenchIncomplete` when there is no reserve keeper, or fewer than `size` reserves.
    """
    started = set(starts)
    reserves = [p for p in roster if p.id not in started]

    keepers = sorted(
        (p for p in reserves if gk_role in p.roles),
        key=lambda p: value.get(p.id, 0.0),
        reverse=True,
    )
    if not keepers:
        raise BenchIncomplete("no reserve goalkeeper for bench slot 0")
    keeper = keepers[0]

    outfield = sorted(
        (p for p in reserves if p.id != keeper.id),
        key=lambda p: value.get(p.id, 0.0),
        reverse=True,
    )
    bench = [keeper.id, *(p.id for p in outfield[: size - 1])]
    if len(bench) < size:
        raise BenchIncomplete(f"only {len(bench)} reserves available, need {size}")
    return bench
