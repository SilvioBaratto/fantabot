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
from dataclasses import dataclass
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
    """

    signals: Mapping[str, float]
    prior_mean: float
    base_variance: float
    no_history_variance: float
    no_history: frozenset[str] = frozenset()

    def value(self, player_id: str) -> PlayerValue:
        if player_id not in self.signals:
            return PlayerValue(self.prior_mean, self.no_history_variance)
        variance = (
            self.no_history_variance if player_id in self.no_history else self.base_variance
        )
        return PlayerValue(self.signals[player_id], variance)
