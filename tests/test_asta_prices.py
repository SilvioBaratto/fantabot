"""Expected clearing price per player. The pure reducer is tested here; the DB query
(`expected_prices`) is a thin shell verified in the db tier / by manual run.

Prices come from the loaded Mantra auctions of our exact league shape (8 teams, 500
credits) — 6,709 sales over 518 players — so they are directly comparable without budget
normalization.
"""

from __future__ import annotations

import ast
import pathlib

from fantabot.asta_engine.prices import Sale, mean_prices


def test_mean_price_per_player() -> None:
    sales = [Sale("1", 10), Sale("1", 20), Sale("2", 5)]
    assert mean_prices(sales) == {"1": 15.0, "2": 5.0}


def test_no_sales_is_an_empty_map() -> None:
    assert mean_prices([]) == {}


def _module_imports(name: str) -> set[str]:
    pkg = pathlib.Path(__file__).resolve().parents[1] / "src" / "fantabot" / "asta_engine"
    tree = ast.parse((pkg / name).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_value_is_pure_and_prices_is_the_db_edge() -> None:
    assert not any(m.startswith("fantabot.db") for m in _module_imports("value.py"))
    # prices.py is the I/O shell — it is allowed to reach the repository.
    assert any(m.startswith("fantabot.db") for m in _module_imports("prices.py"))
