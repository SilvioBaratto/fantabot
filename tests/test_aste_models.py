"""The auction tables, asserted against ``Base.metadata`` rather than a database.

No socket, so these run in the default tier. What they pin is the handful of
schema decisions that are expensive to discover later.
"""

from __future__ import annotations

import pytest

from fantabot.adapters.persistence.models import Asta, AstaAssignment, AstaEvent, Base

TABLES = {"asta", "asta_event", "asta_assignment"}


def test_the_three_tables_are_registered() -> None:
    """A model missing from ``models/__init__`` is invisible to autogenerate,
    which then proposes dropping its table."""
    assert set(Base.metadata.tables) >= TABLES


def test_asta_type_is_a_column_not_a_filter() -> None:
    """The whole coverage goal of this phase: Classic is stored, not excluded.
    Filtering is a query concern and must not be baked into the schema."""
    assert "asta_type" in Asta.__table__.columns
    assert not Asta.__table__.columns["asta_type"].nullable


def test_an_assignment_points_at_a_player_but_need_not_find_one() -> None:
    """`fantacalcio_id` carries the foreign key to `players.id`, and is nullable
    on purpose: on 2026-08-26, 2 of 407 auctioned players were signings more
    recent than our last scrape. A NOT NULL column would have refused the row and
    lost the price."""
    column = AstaAssignment.__table__.columns["fantacalcio_id"]
    assert column.nullable, "a newly-signed player must not cost us the assignment"
    target = next(iter(column.foreign_keys)).target_fullname
    assert target == "players.id"


def test_the_raw_frame_survives_a_reducer_bug() -> None:
    """`asta_event` keeps the payload whole. Storing only the interpretation
    would make a parser fix require a re-collection, and an evening does not
    come back."""
    assert "payload" in AstaEvent.__table__.columns


def test_an_assignment_keeps_its_ladder() -> None:
    assert "ladder" in AstaAssignment.__table__.columns


@pytest.mark.parametrize("table", sorted(TABLES))
def test_every_auction_table_records_when_it_was_written(table: str) -> None:
    """`created_at` on all three; `updated_at` on all but the append-only one.

    `asta_event` dropped `updated_at` on 2026-08-30 because it differed from
    `created_at` on **0 of 486,803 rows** — the table is append-only, so the second
    timestamp was 3.8 MB restating the first. The other two are upserted and their
    `updated_at` moves, so they keep it.
    """
    columns = set(Base.metadata.tables[table].columns.keys())
    assert "created_at" in columns
    if table != "asta_event":
        assert "updated_at" in columns, f"{table} is upserted; it must record the update"
    else:
        assert "updated_at" not in columns, (
            "asta_event is append-only — updated_at was measured identical to "
            "created_at on every row and dropped"
        )
