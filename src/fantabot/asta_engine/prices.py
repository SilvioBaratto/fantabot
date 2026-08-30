"""Expected clearing price per player, from the loaded real Mantra auctions.

`mean_prices` averages a player's observed sale prices. Prices are drawn from auctions
of our exact league shape (8 teams, 500 credits) so they are directly comparable — no
budget normalization needed at v1. A player never sold in that set has no entry, and the
caller falls back to a prior.

**The query that supplies these sales lives in `plan.py`.** It used to live here, as an
`expected_prices` shell whose repository import sat inside the function body, which made
this module read as pure at every level a reader or a grep would check while it reached
Postgres on every call. `plan.py` was its only caller and already holds the other two
reads, so moving it there removed an indirection rather than adding a file.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Sale:
    """One observed sale: a player and the credits he cleared for."""

    player_id: str
    price: int


def mean_prices(sales: Iterable[Sale]) -> dict[str, float]:
    """Mean clearing price per player across the observed sales. Pure."""
    totals: dict[str, list[int]] = {}
    for sale in sales:
        totals.setdefault(sale.player_id, []).append(sale.price)
    return {player_id: sum(prices) / len(prices) for player_id, prices in totals.items()}
