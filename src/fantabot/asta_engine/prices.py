"""Expected clearing price per player, from the loaded real Mantra auctions.

The pure reducer ``mean_prices`` averages a player's observed sale prices; the thin
``expected_prices`` shell pulls those sales from Postgres through the repository. Prices
are drawn from auctions of our exact league shape (8 teams, 500 credits) so they are
directly comparable — no budget normalization needed at v1. A player never sold in that
set has no entry, and the caller falls back to a prior.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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


def expected_prices(session: Session, *, budget: int = 500, num_teams: int = 8) -> dict[str, float]:
    """The I/O edge: fetch Mantra sales of our league shape and reduce to a mean per player."""
    from fantabot.db.repositories.aste import AsteRepository

    sales = [
        Sale(player_id, price)
        for player_id, price in AsteRepository(session).mantra_clearing_sales(
            budget=budget, num_teams=num_teams
        )
    ]
    return mean_prices(sales)
