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

from sqlalchemy import Index

import fantabot.db.models  # noqa: F401  -- registers every table on Base.metadata
from fantabot.db.base import Base

PROBE = "_probe_match_grain"


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


def _probe_indexes() -> list[Index]:
    return sorted(Base.metadata.tables[PROBE].indexes, key=lambda i: i.name or "")


def test_probe_has_a_surrogate_primary_key() -> None:
    """Postgres forbids a nullable column in a primary key, so the match grain
    cannot use SPEC's ``(stagione, giornata, player_id)`` directly."""
    table = Base.metadata.tables[PROBE]
    assert [c.name for c in table.primary_key.columns] == ["id"]
    assert table.c.player_id.nullable is True


def test_probe_declares_exactly_two_partial_unique_indexes() -> None:
    """Disjoint predicates: every row is covered by exactly one of them."""
    indexes = _probe_indexes()
    assert len(indexes) == 2
    assert all(index.unique for index in indexes)

    predicates = sorted(
        str(index.dialect_options["postgresql"]["where"]) for index in indexes
    )
    assert predicates == ["player_id IS NOT NULL", "player_id IS NULL"]


def test_probe_carries_an_array_column() -> None:
    """The other construct the schema cannot avoid: ``;``-joined role codes
    become ``text[]``, and autogenerate has to round-trip that type."""
    assert Base.metadata.tables[PROBE].c.ruoli_codice.type.__class__.__name__ == "ARRAY"
