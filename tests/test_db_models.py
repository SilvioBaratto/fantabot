"""Metadata-level assertions about the schema. No engine, no connection.

These run in the default (socket-free) tier deliberately: everything asserted
here is a property of ``Base.metadata``, built at import time. A test that
needed a database to check a column's nullability would be pinning the
migration rather than the model.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from sqlalchemy import Index

import fantabot.db.models  # noqa: F401  -- registers every table on Base.metadata
from fantabot.db.base import Base

MATCH_TABLES = ("voti", "bonus_malus")


def test_naming_convention_covers_every_constraint_kind() -> None:
    """Autogenerate emits unnamed constraints without this, and ``alembic
    downgrade base`` cannot drop what it cannot name — SPEC criterion 4."""
    assert set(Base.metadata.naming_convention) == {"ix", "uq", "ck", "fk", "pk"}


def test_importing_models_opens_no_socket() -> None:
    """A module-scope ``create_engine`` would make ``fantabot --help`` connect.

    Run in a fresh interpreter rather than by deleting entries from
    ``sys.modules``: re-importing in-process rebinds ``Base`` to a new class and
    silently invalidates every other test in this module.
    """
    script = textwrap.dedent(
        """
        import socket

        def boom(*args, **kwargs):
            raise AssertionError("a connection was opened at import time")

        # Patch connect rather than socket.socket itself: ssl.SSLSocket
        # subclasses socket.socket, so replacing the class with a function
        # breaks the stdlib before our own import is even reached. Creating a
        # socket object is harmless anyway; connecting is the observable.
        socket.socket.connect = boom
        socket.socket.connect_ex = boom
        socket.create_connection = boom

        import fantabot.db
        import fantabot.db.models
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def _partial_indexes(table: str) -> list[Index]:
    return sorted(
        (index for index in Base.metadata.tables[table].indexes if index.unique),
        key=lambda index: index.name or "",
    )


@pytest.mark.parametrize("table", MATCH_TABLES)
def test_match_grain_uses_a_surrogate_primary_key(table: str) -> None:
    """Postgres forbids a nullable column in a primary key, so the match grain
    cannot use SPEC's ``(stagione, giornata, player_id)`` directly — and the
    3039 coach rows per file would collide with each other anyway."""
    metadata_table = Base.metadata.tables[table]

    assert [column.name for column in metadata_table.primary_key.columns] == ["id"]
    assert metadata_table.c.player_id.nullable is True


@pytest.mark.parametrize("table", MATCH_TABLES)
def test_match_grain_declares_two_disjoint_partial_unique_indexes(table: str) -> None:
    """Every row is covered by exactly one: with a player, or without."""
    indexes = _partial_indexes(table)

    assert len(indexes) == 2
    predicates = sorted(
        str(index.dialect_options["postgresql"]["where"]) for index in indexes
    )
    assert predicates == ["player_id IS NOT NULL", "player_id IS NULL"]


def test_the_corrupt_team_column_is_named_raw_and_never_keyed_on() -> None:
    """scripts/analyze_qi_bias_by_team.py documents that the scraper labels
    every row in a match block with the fixture's home team, so the column
    cannot say which side a player played for."""
    for table in MATCH_TABLES:
        metadata_table = Base.metadata.tables[table]
        assert "squadra_raw" in metadata_table.c
        assert "squadra" not in metadata_table.c

        keyed = {
            column.name
            for index in metadata_table.indexes
            for column in index.columns
        } | {column.name for column in metadata_table.primary_key.columns}
        assert "squadra_raw" not in keyed
        assert not [
            fk for fk in metadata_table.foreign_keys if "squadra" in fk.parent.name
        ]


def test_the_throwaway_probe_is_gone() -> None:
    """It existed only to prove ARRAY and partial indexes round-trip. Left in,
    it would be a table in db-check that appears nowhere in SPEC's Schema."""
    assert "_probe_match_grain" not in Base.metadata.tables


def test_quotazioni_still_carries_the_array_column() -> None:
    """The construct the probe was proving. It now lives on a real table."""
    column = Base.metadata.tables["quotazioni"].c.ruoli_codice
    assert column.type.__class__.__name__ == "ARRAY"


def test_player_sentiment_column_set_matches_the_csv_columns() -> None:
    """SPEC: "columns exactly news/store.py:COLUMNS". One rename — ``id``
    becomes ``player_id`` so it can carry the foreign key — and nothing is
    dropped as derivable: n_fonti stays even though it is cardinality(fonti),
    because dropping it is a deviation to ask about rather than a free win."""
    from fantabot.news.store import COLUMNS

    table = Base.metadata.tables["player_sentiment"]
    declared = {column.name for column in table.c} - {"created_at", "updated_at"}
    expected = {("player_id" if name == "id" else name) for name in COLUMNS}

    assert declared == expected


def test_player_sentiment_is_keyed_on_the_resume_index() -> None:
    """(data_run, player_id) is exactly what store.existing_keys returns, so
    resume becomes an upsert with the same observable behaviour."""
    table = Base.metadata.tables["player_sentiment"]

    assert [column.name for column in table.primary_key.columns] == [
        "data_run",
        "player_id",
    ]


def test_every_score_column_keeps_two_decimal_places() -> None:
    """build_row writes "%.2f". numeric(3,2) preserves it; a float would not."""
    from fantabot.db.models.sentiment import SCORE_COLUMNS

    table = Base.metadata.tables["player_sentiment"]
    for name in (*SCORE_COLUMNS, "deriva_ruolo"):
        column = table.c[name]
        assert column.type.__class__.__name__ == "Numeric"
        assert (column.type.precision, column.type.scale) == (3, 2)
        assert column.nullable is False


def test_deriva_ruolo_is_numeric_and_not_boolean() -> None:
    """The ruling of 2026-08-26: a flag collapses drifted()'s ranking."""
    column = Base.metadata.tables["player_sentiment"].c.deriva_ruolo

    assert column.type.__class__.__name__ != "Boolean"


def test_only_fonti_became_an_array() -> None:
    """ruolo_campo and ruoli_mantra stay ";"-joined text: SPEC's departures
    table lists only ruoli_codice, fonti and flags as text[], and build_row
    sorts these two so the cell is comparable to its neighbour."""
    table = Base.metadata.tables["player_sentiment"]

    assert table.c.fonti.type.__class__.__name__ == "ARRAY"
    assert table.c.ruolo_campo.type.__class__.__name__ == "Text"
    assert table.c.ruoli_mantra.type.__class__.__name__ == "Text"


SCHEMA_TABLES = frozenset(
    {
        "players",
        "teams",
        "quotazioni",
        "statistiche",
        "qi_bias",
        "target_price",
        "voti",
        "bonus_malus",
        "player_sentiment",
        "bot_state",
        "auction_bids",
        "league_snapshot",
        "league_team_snapshot",
        "league_player_pool",
    }
)


def test_every_table_spec_names_exists_and_nothing_else_does() -> None:
    """SPEC criterion 4's first half, checked against the metadata rather than
    a database: the schema is complete, and it has not grown anything extra."""
    assert set(Base.metadata.tables) == SCHEMA_TABLES


def test_snapshots_are_keyed_from_captured_at_outward() -> None:
    """Append-only and time-stamped: the point of the tables is the drift, so
    the timestamp leads the key rather than being an attribute of it."""
    for table in ("league_snapshot", "league_team_snapshot", "league_player_pool"):
        columns = [c.name for c in Base.metadata.tables[table].primary_key.columns]
        assert columns[0] == "captured_at", table
        assert "league_id" in columns, table


def test_the_league_pool_has_no_foreign_key_to_players() -> None:
    """The two lists come from different places — players from the scraped
    CSVs, this from the live API — and do not have to agree. A constraint would
    make a snapshot fail because the seed is a week stale, which is the drift
    the table exists to record."""
    assert Base.metadata.tables["league_player_pool"].foreign_keys == set()


def test_the_snapshot_tables_have_no_importer_and_say_why() -> None:
    """They ship empty on purpose. SPEC open question 5 decides the producer,
    and adding an HTTP client is on SPEC's Ask-first list — so the absence is a
    decision, and the docstring has to be the thing that says so."""
    from fantabot.db import importers
    from fantabot.db.models import league

    assert "league_snapshot" not in importers.names()
    assert "open question 5" in league.__doc__.lower()  # type: ignore[union-attr]
