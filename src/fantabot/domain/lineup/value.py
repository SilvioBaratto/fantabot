"""Each player's ranking signal: `fvmma` tilted by news sentiment. Pure.

The objective the matcher maximises is `sum(score)` over the started players — linear, which
is what lets the assignment be solved exactly. `score` is that per-player term. The sentiment
multiplier comes from `domain/asta/sentiment.effect_by_id` (the same gates the auction uses),
computed by the caller against an `as_of` it owns; passing it in keeps this module clock-free.
With no effect the score is the raw `fvmma` — the `--no-sentiment` ablation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fantabot.domain.lineup.models import RosterPlayer


def score(
    roster: Sequence[RosterPlayer],
    *,
    effect: Mapping[int, float] | None = None,
) -> dict[int, float]:
    """`{player_id: fvmma * multiplier}`. A player absent from `effect` scores at `1.0`."""
    multipliers = effect or {}
    return {player.id: player.fvmma * multipliers.get(player.id, 1.0) for player in roster}
