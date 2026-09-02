"""Each player's ranking signal — the per-player objective term. Pure.

The objective the matcher maximises is `sum(score)` over the started players; `score` is that
per-player term. It is the sourced value on `RosterPlayer.fvmma` — which in this phase carries
the platform's own `indexCompare` rating (`docs/leghe-api.md`), because the scraped
`quotazioni`/sentiment ids do not join the league roster. A sentiment tilt is *not* applied:
that seam was removed once the id mismatch made it unwireable; add it back here if a joinable
signal ever exists.
"""

from __future__ import annotations

from collections.abc import Sequence

from fantabot.domain.lineup.models import RosterPlayer


def score(roster: Sequence[RosterPlayer]) -> dict[int, float]:
    """`{player_id: value}` — each player's value signal, the weight the matcher maximises."""
    return {player.id: player.fvmma for player in roster}
