"""Each player's ranking signal — the per-player objective term. Pure.

The objective the matcher maximises is `sum(score)` over the started players; `score` is that
per-player term. It is the sourced value on `RosterPlayer.fvmma` — which carries the
platform's own `indexCompare` rating (`docs/leghe-api.md`). No sentiment tilt is applied.

This used to say the tilt was removed because the scraped `quotazioni`/sentiment ids do not
join the league roster. They do: `match_grain`, `quotazioni` and `players` hold 596 of the
lega's 597 pool players by the same id, and `player_sentiment` 554 (measured 2026-09-22).
The better signal is the projection of phase `lineup-theory`, not a tilt on this one.
"""

from __future__ import annotations

from collections.abc import Sequence

from fantabot.domain.lineup.models import RosterPlayer


def score(roster: Sequence[RosterPlayer]) -> dict[int, float]:
    """`{player_id: value}` — each player's value signal, the weight the matcher maximises."""
    return {player.id: player.fvmma for player in roster}
