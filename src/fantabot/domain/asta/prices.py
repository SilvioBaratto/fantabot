"""Expected clearing price per player, from the loaded real Mantra auctions.

`mean_prices` averages a player's observed sale prices. Prices are drawn from auctions
of our exact league shape (8 teams, 500 credits) so they are directly comparable — no
budget normalization needed at v1. A player never sold in that set has no entry, and the
caller falls back to a prior.

**The query that supplies these sales lives in `application/asta_planner.py`.** It used
to live here, as an `expected_prices` shell whose repository import sat inside the function
body, which made this module read as pure at every level a reader or a grep would check
while it reached Postgres on every call. That caller was its only one and already holds the
other two reads, so moving it there removed an indirection rather than adding a file. It was
called `plan.py` then, which is how this paragraph named it until 2026-09-24.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


class NoCorpus(LookupError):
    """No recorded auction of the shape a plan asked to be priced against.

    **A refusal and not a fallback**, and the reason is measured. `mean_prices` returns
    `{}` for an empty corpus, an empty `prices` mapping is a perfectly legal argument, and
    `DEFAULT_PRICE = 1` then applies to everybody — so the optimizer's budget constraint
    becomes vacuous and nothing raises. On 2026-09-05 that bought a 25-man Classic rosa for
    **25 credits of 500**, with 22 of its 25 slots differing from the corpus-priced plan.
    A plan silently built against no costs is worse than no plan.

    **And not a nearest-shape fallback either.** Prices are comparable only within one
    shape — that is the whole reason the corpus is filtered by it, and why no budget
    normalization is applied. Pricing a 10x1000 room off 8x500 sales would be an answer
    with no error bar, offered where a refusal was available.

    Lives here, in the pure module that owns what a price corpus is, so the repository that
    raises it and the application that catches it can both name it without either importing
    the other.
    """

    def __init__(self, shape: str, recorded: Sequence[str]) -> None:
        listed = ", ".join(recorded) if recorded else "none at all"
        # The recorded shapes are in the message because the operator's next move is to
        # pick one, and a refusal that does not say what *is* available sends them to SQL.
        super().__init__(
            f"no recorded auctions of shape {shape}; the corpus holds {listed}. "
            "Prices are comparable only within a shape, so this refuses rather than "
            "pricing off somebody else's game."
        )
        self.shape = shape
        self.recorded = tuple(recorded)


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
