"""Player value: the interface the optimizer prices, and a naive v1 implementation. Pure.

The optimizer needs, per player, a mean value and a variance - the mean-variance
objective reads both.
`ValueModel` is that contract. `NaiveValueModel` is the scaffolding implementation: it
carries a per-player value signal (v1 feeds it the market quotazione / target price as a
rough proxy for expected season points) and reports a flat variance, widened for players
the market priced but who have no playing history, and widest — shrunk to a prior — for
players it has never seen.

The real value layer is a skfolio Black-Litterman posterior (mean + covariance); it slots
in behind this same Protocol with no change to the optimizer. Keeping the naive model
honest about its uncertainty — a wide band, not a fabricated point — is what lets the
optimizer be built and trusted before that lands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PlayerValue:
    """One player's value as the optimizer reads it: an expected value and its variance."""

    mean: float
    variance: float


@runtime_checkable
class ValueModel(Protocol):
    """What the optimizer asks of any value layer, naive or Black-Litterman."""

    def value(self, player_id: str) -> PlayerValue: ...


@dataclass(frozen=True)
class NaiveValueModel:
    """A value from a single per-player signal, with uncertainty that reflects the data.

    ``signals`` maps player id → the proxy value (v1: quotazione / target price).
    ``no_history`` is the subset priced by the market but absent from the point history —
    same mean, wider band. A player missing from ``signals`` shrinks to ``prior_mean`` with
    ``no_history_variance``, the widest band, rather than being assigned a fabricated value.

    ``variances`` is the per-player band, defaulting to ``base_variance`` for anyone absent.
    Without it every player carried the same variance, which made ``lam`` nearly inert — a
    risk penalty that is identical for every candidate cannot change which candidate wins,
    so the mean-variance objective quietly degenerated to maximizing the mean.

    ``no_history`` is a **floor** on that band, not a replacement for it. Never having been
    priced is the *minimum* ignorance about a player, so the model takes whichever of the
    two is wider. Letting it short-circuit instead narrowed the band for anyone the
    sentiment layer had already widened past it — a drifted player, say — which is the
    fail-open direction on a rule that may only ever widen. It also handed every
    no-history player the identical flat band, the degenerate ``lam`` case that per-player
    variance exists to remove.
    """

    signals: Mapping[str, float]
    prior_mean: float
    base_variance: float
    no_history_variance: float
    no_history: frozenset[str] = frozenset()
    variances: Mapping[str, float] = field(default_factory=dict)

    def value(self, player_id: str) -> PlayerValue:
        if player_id not in self.signals:
            return PlayerValue(self.prior_mean, self.no_history_variance)
        variance = self.variances.get(player_id, self.base_variance)
        if player_id in self.no_history:
            variance = max(self.no_history_variance, variance)
        return PlayerValue(self.signals[player_id], variance)
