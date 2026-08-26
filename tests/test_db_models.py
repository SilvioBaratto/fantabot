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
