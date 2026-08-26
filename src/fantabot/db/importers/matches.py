"""Shared machinery for the two match-grain importers.

**The mechanic that matters.** A single ``INSERT ... ON CONFLICT`` names exactly
one conflict target, and these tables have two — disjoint partial unique indexes,
one for rows with a player and one for coach rows. A bare
``ON CONFLICT (stagione, giornata, player_id)`` raises ``there is no unique or
exclusion constraint matching the ON CONFLICT specification``, because a partial
index only matches when the statement repeats its predicate. So each file is
inserted in **two passes**, each naming its own index and its own ``index_where``.

50,634 rows per file, so both passes are chunked. The chunk size is a
compromise: large enough that round-trips do not dominate, small enough that a
single statement's parameter list stays well inside Postgres's 65,535 bound
(19 columns x 2000 rows = 38,000 parameters).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import Table, inspect, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

CHUNK = 2000

_PLAYER_ROWS = text("player_id IS NOT NULL")
_COACH_ROWS = text("player_id IS NULL")


def parse_date(raw: str) -> date:
    """``"01/02/2025"`` -> ``date(2025, 2, 1)``. Italian order, not American."""
    return datetime.strptime(raw.strip(), "%d/%m/%Y").date()


def parse_time(raw: str) -> time | None:
    """``"12:30"`` -> ``time(12, 30)``; empty -> ``None``.

    bonus_malus has no kick-off time at all, and voti has one for every row.
    """
    value = raw.strip()
    return datetime.strptime(value, "%H:%M").time() if value else None


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
