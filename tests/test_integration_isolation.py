"""The `db` tier runs against the development database, which holds real data.

That is the point — the contracts these tests pin are about ordering, windowing and NULL
handling, which a fake session cannot settle. It is also the hazard: the database is
*shared*, so a test that borrows a real row is not isolated from whatever `news-fetch` or
the scrapers last wrote, and its result depends on the calendar.

This file is the standing guard, checked statically so it runs in the default socket-free
tier. It exists because the repository has already paid for this lesson once: the
`canary_player` fixture in ``test_news_fetch_write.py`` records that ``pytest -m db`` was
deleting a real player's weekly reading, twice per test, and CLAUDE.md's rule is that a
past Wednesday cannot be regenerated. That fixture fixed one file; the rule belongs to all
of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).resolve().parent / "integration"

#: The borrow: *selecting ids* out of the table to use as test subjects. Whichever ids come
#: back are real players with real readings, so the test collides with production rows on
#: `(data_run, player_id)` and reads back somebody else's data.
#:
#: Narrow on purpose. ``SELECT count(*) FROM players`` asserts a table-level invariant and
#: ``DELETE FROM players WHERE id = :p`` is a synthetic fixture cleaning up after itself —
#: both are correct, and a guard that flagged them would be turned off rather than obeyed.
BORROW = "SELECT id FROM players"


def _code_strings(path: Path) -> list[str]:
    """Every string literal that is not a docstring.

    Docstrings are excluded deliberately: the two fixtures that already fixed this bug
    *describe* the old query in prose, and a grep-based guard would flag the very comments
    warning against it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.mark.parametrize(
    "path", sorted(INTEGRATION.glob("test_*.py")), ids=lambda p: p.name
)
def test_no_integration_test_borrows_a_real_player_id(path: Path) -> None:
    """Use a synthetic id instead — one with no `quotazioni` row, so `load_pool` never
    returns it and no real reading can share its key."""
    offenders = [text for text in _code_strings(path) if BORROW in text]

    assert offenders == [], (
        f"{path.name} borrows real player ids: {offenders}. "
        "Use the `synthetic_players` fixture (tests/conftest.py) — the db tier shares a "
        "database "
        "with real data, and a borrowed row collides with production."
    )
