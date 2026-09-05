"""`fantabot db dump` after the compose stack was deleted.

The dump used to shell `docker compose exec -T db pg_dump`, which addressed the container
by service name. With no compose stack there is no container, so the dump runs `pg_dump`
directly against the configured DSN — and the DSN is the bundled server's unix socket,
which `pg_dump` accepts as a libpq connection URI once the SQLAlchemy driver suffix is
gone (`postgresql+psycopg2://` is not a scheme libpq knows).

The argv is a pure function of the DSN, so it is pinned here without running anything.
"""

from __future__ import annotations

from fantabot.interface.app import _pg_dump_argv

SOCKET = "postgresql+psycopg2://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"


def test_the_driver_suffix_is_stripped_for_libpq() -> None:
    """`postgresql+psycopg2://` is SQLAlchemy's spelling; libpq rejects it."""
    argv = _pg_dump_argv(SOCKET)

    assert argv[0] == "pg_dump"
    assert argv[-1] == "postgresql://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"
    assert "+psycopg2" not in " ".join(argv)


def test_the_dump_stays_in_the_custom_format() -> None:
    """`-Fc` is what makes pg_restore selective; a plain SQL dump is not the same artefact."""
    assert "-Fc" in _pg_dump_argv(SOCKET)


def test_a_tcp_dsn_survives_unchanged_apart_from_the_driver() -> None:
    argv = _pg_dump_argv("postgresql+psycopg2://postgres:pw@localhost:5432/fantabot")

    assert argv[-1] == "postgresql://postgres:pw@localhost:5432/fantabot"


def test_no_container_is_addressed() -> None:
    """The regression this file exists for: `docker compose exec -T db` named a service."""
    assert "docker" not in _pg_dump_argv(SOCKET)
