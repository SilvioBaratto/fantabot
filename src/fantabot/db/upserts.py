"""Shared machinery for the two match-grain tables, ``voti`` and ``bonus_malus``.

**The mechanic that matters.** A single ``INSERT ... ON CONFLICT`` names exactly
one conflict target, and these tables have two — disjoint partial unique indexes,
one for rows with a player and one for coach rows. A bare
``ON CONFLICT (stagione, giornata, player_id)`` raises ``there is no unique or
exclusion constraint matching the ON CONFLICT specification``, because a partial
index only matches when the statement repeats its predicate. So each write is
made in **two passes**, each naming its own index and its own ``index_where``.

The coach rows are the reason the second pass exists: roughly 760 per season,
3,039 across the four scraped so far, every one with a NULL ``player_id``. If a
scrape reports the player pass and not the coach pass, that is the symptom.

50,634 rows per table per full scrape, so both passes are chunked. The chunk
size is a compromise: large enough that round-trips do not dominate, small
enough that a single statement's parameter list stays well inside Postgres's
65,535 bound (19 columns x 2000 rows = 38,000 parameters).

**Load order is a foreign-key constraint, not a style preference.** ``players``
and ``teams`` have no outbound foreign keys and must exist before anything that
points at them, so a full rebuild runs ``scrape_quotazioni`` (which writes both)
before ``scrape_voti``. Writing the facts first is a foreign-key violation, not
a slow run. This paragraph used to live in the seed registry's docstring, which
was the only place it was written down.

Every write here upserts rather than inserts. A killed scrape is restarted, not
repaired.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import Table, inspect, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

CHUNK = 2000

_PLAYER_ROWS = text("player_id IS NOT NULL")
_COACH_ROWS = text("player_id IS NULL")


def chunked(rows: Sequence[dict[str, Any]], size: int = CHUNK) -> Iterator[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


def table_for(model: type[Any]) -> Table:
    """The mapped Table for a declarative model, typed as one.

    ``Model.__table__`` is declared as ``FromClause`` on ``DeclarativeBase``,
    which ``insert()`` will not accept under ``mypy --strict``.
    """
    table = inspect(model).local_table
    assert isinstance(table, Table)
    return table


def upsert_two_passes(
    session: Session,
    model: type[Any],
    rows: Sequence[dict[str, Any]],
    *,
    updatable: Sequence[str],
) -> None:
    """Upsert match-grain rows, once per partial index.

    Splitting on ``player_id`` is not an optimisation: it is what makes the
    statement legal at all, since each pass has to repeat the predicate of the
    index it targets.
    """
    table = table_for(model)

    passes = (
        (
            [row for row in rows if row["player_id"] is not None],
            ["stagione", "giornata", "player_id"],
            _PLAYER_ROWS,
        ),
        (
            [row for row in rows if row["player_id"] is None],
            ["stagione", "giornata", "nome"],
            _COACH_ROWS,
        ),
    )

    for subset, index_elements, index_where in passes:
        for batch in chunked(subset):
            statement = insert(table).values(batch)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=index_elements,
                    index_where=index_where,
                    set_={column: statement.excluded[column] for column in updatable},
                )
            )
