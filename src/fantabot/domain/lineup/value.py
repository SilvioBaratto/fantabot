"""Each player's ranking signal — the per-player objective term. Pure.

The objective the matcher maximises is `sum(score)` over the started players; `score` is that
per-player term. It is the sourced value on `RosterPlayer.fvmma` — which carries the
platform's own `indexCompare` rating (`docs/leghe-api.md`). No sentiment tilt is applied.

This used to say the tilt was removed because the scraped `quotazioni`/sentiment ids do not
join the league roster. They do: `match_grain`, `quotazioni` and `players` hold 596 of the
lega's 597 pool players by the same id, and `player_sentiment` 554 (measured 2026-09-22).
The better signal is the projection of phase `lineup-theory`, not a tilt on this one.

The projection's term is `sub_aware`: a starter who gets no vote is replaced by the auto-sub,
so what he is worth is `p·μ + (1-p)·v`, not `p·μ`. SPEC A17(3) makes `v` per slot — the best
reserve covering it — and that needs a matcher that weighs (slot, player) pairs, which is
T22's. Here `v` is one number, the **next man up**: the best expected score outside the XI.

The two differ, and not by a constant. `p·μ + (1-p)·v` is `v + p·(μ - v)`, and every module
fields exactly 11, so `v` shifts every module's total alike and the ranking is really
`p·(μ - v)`: value over the man who would come on. It moves **toward** risk, because `p·μ`
prices a no-show at 0 while the auto-sub is worth `v`. A star at p 0.5, μ 9 is 4.5 on `p·μ`
and loses to a p 1, μ 5 journeyman; with a 4.0 man on the bench he is worth 6.5 and starts
ahead of him. The question being asked is his edge over the replacement, not over nothing —
and `v` is what makes a benched roster worth having.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeVar

from fantabot.domain.lineup.models import RosterPlayer

#: Player ids are ints in the app; the term itself only needs them to be keys.
Player = TypeVar("Player")

#: Starters in a formation: the XI the replacement level sits behind.
XI = 11


def score(roster: Sequence[RosterPlayer]) -> dict[int, float]:
    """`{player_id: value}` — each player's value signal, the weight the matcher maximises."""
    return {player.id: player.fvmma for player in roster}


def replacement_level(expected: Mapping[Player, float], *, starters: int = XI) -> float:
    """What the auto-sub is worth: the best expected score outside the `starters` best.

    A roster too short to bench anyone takes its own worst, and an empty one is 0 — neither
    is a real lineup, and both keep `sub_aware` total rather than dividing by a missing man.
    """
    if not expected:
        return 0.0
    ranked = sorted(expected.values(), reverse=True)
    return ranked[min(starters, len(ranked) - 1)]


def sub_aware(
    mu: Mapping[Player, float], p: Mapping[Player, float], *, starters: int = XI
) -> dict[Player, float]:
    """`p·μ + (1-p)·v` per projected player, `v` the replacement level of `p·μ`."""
    expected = {player: p[player] * value for player, value in mu.items()}
    v = replacement_level(expected, starters=starters)
    return {player: value + (1.0 - p[player]) * v for player, value in expected.items()}
