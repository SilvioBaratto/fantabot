"""The scraping readers actually execute against the real schema.

**Why this is separate from `test_invariants.py`.** That file reads
`db/scraping.py` as *text* and checks its hand-written column lists against the
live catalogue — which catches a renamed or dropped column, and catches nothing
about whether the SQL runs. These three readers were never executed by any test:
`grep -rn 'load_bias_rows' tests/` returned nothing before this file existed.
They lived in `scripts/`, which `ruff` does not lint, `mypy` does not type
(`files = ["src"]`) and no test imported, so "it parses" was the whole of their
verification.

They are also the readers `target_price` fits its regressions over, and that model
is the only thing in the repo whose numbers get spent as real credits.

**What is asserted, and what deliberately is not.** Row *counts* are not: the
scrapers read a live site and CLAUDE.md records that the counts in
`data/README.md` are floors, not fixtures. What is asserted is the shape the
callers depend on — that the query runs, that it returns the declared type, and
that the fields `target_price` reads are populated rather than silently None.

`load_bias_rows` reads `qi_bias`, which becomes a **view** in a later migration.
That is the point of keeping this test: a view that does not answer the same
question fails here rather than in a pricing run.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from fantabot.db.scraping import (
    BiasRow,
    PlayerQuote,
    PriorStats,
    load_bias_rows,
    load_prior_stats,
    load_quotes,
)

pytestmark = pytest.mark.db

LISTONI = ("classic", "mantra")


@pytest.mark.parametrize("listone", LISTONI)
def test_prior_stats_load_and_are_shaped_as_declared(db_session: Session, listone: str) -> None:
    stats = load_prior_stats(db_session, listone)

    assert stats, f"no prior stats for {listone} — the readers are the pricing model's input"
    key, value = next(iter(stats.items()))
    assert isinstance(key, tuple) and len(key) == 2, "keyed by (player_id, stagione)"
    assert isinstance(value, PriorStats)
    # media_fantavoto is the regressor. NULL is a real value here — the site writes
    # "0,0" for a player it has no average for — but every row cannot be NULL.
    assert any(s.media_fantavoto is not None for s in stats.values())


@pytest.mark.parametrize("listone", LISTONI)
def test_bias_rows_load_from_whatever_qi_bias_is(db_session: Session, listone: str) -> None:
    """Table today, view after the normalization migration. The caller cannot tell."""
    rows = load_bias_rows(db_session, listone)

    assert rows, f"no qi_bias rows for {listone}"
    assert all(isinstance(r, BiasRow) for r in rows)
    assert all(r.qi > 0 for r in rows), "qi is the divisor of pct_delta; zero would be a bug"


@pytest.mark.parametrize("listone", LISTONI)
def test_quotes_load_and_carry_a_role(db_session: Session, listone: str) -> None:
    quotes = load_quotes(db_session, listone)

    assert quotes
    assert all(isinstance(q, PlayerQuote) for q in quotes)
    assert all(q.role for q in quotes), "the role string is what the model buckets on"


def test_the_readers_order_their_rows(db_session: Session) -> None:
    """Two calls return the same sequence.

    `db/scraping.py`'s docstring states the rule and the reason: these used to read
    files, so row order was stable by accident, and `target_price` fits regressions
    over what they return. An unordered scan makes a model's coefficients wobble
    between runs with nothing to show for it.
    """
    first = load_quotes(db_session, "mantra")
    second = load_quotes(db_session, "mantra")

    assert [(q.stagione, q.id) for q in first] == [(q.stagione, q.id) for q in second]
