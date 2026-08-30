"""Expected clearing price per player: the reducer.

Prices come from the loaded Mantra auctions of our exact league shape (8 teams, 500
credits) — 6,709 sales over 518 players — so they are directly comparable without budget
normalization. The query that fetches them lives in `plan.py`'s shell and is covered by
the db tier.

This file used to end with a hand-rolled AST check that `prices.py` imported
`fantabot.adapters.persistence` and `value.py` did not. It read one file, did not follow imports, and
asserted the *presence* of a database edge — so it turned red when P11-3 removed that
edge, which is the opposite of what a purity check should do. `tests/test_layers.py` now
makes the same kind of claim across the whole package, transitively, and ratchets in the
right direction.
"""

from __future__ import annotations

from fantabot.asta_engine.prices import Sale, mean_prices


def test_mean_price_per_player() -> None:
    assert mean_prices([Sale("1", 10), Sale("1", 20), Sale("2", 5)]) == {"1": 15.0, "2": 5.0}


def test_no_sales_is_an_empty_map() -> None:
    assert mean_prices([]) == {}
