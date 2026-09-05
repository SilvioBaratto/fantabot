"""Provision a local PostgreSQL server without Docker, via ``pixeltable_pgserver``.

``pixeltable_pgserver`` bundles a real PostgreSQL 18 inside its wheel, so ``uv`` stays
the only prerequisite — there is no runtime download. Behaviour verified live on
Windows/py3.13 (2026-09-03):

* ``get_server(pgdata)`` runs ``initdb`` (first call) and starts the server on a
  **library-chosen** ``127.0.0.1`` port — there is no fixed-port argument, so we do not
  assume ``54321``. We read the real URL from ``server.get_uri(...)`` instead.
* The bundled ``server.psql(...)`` shells out with ``shell=True`` and an unquoted path, so
  it breaks on a data dir under a path with spaces (``C:\\Users\\Baratto Silvio``). We
  therefore create the application database over a normal SQLAlchemy connection, never
  ``psql``.
* fantabot's engine is lazy and reads ``FANTABOT_DATABASE_URL`` at first connect, so we
  export the provisioned URL into the environment and fantabot picks it up unchanged —
  unless the operator exported one first, which wins (see ``PostgresProvisioner``).

The server factory and database creator are injected (defaulting to the real ones) so the
orchestration is unit-testable without starting Postgres — mirroring how fantabot's own
``DatabaseManager`` takes an injectable ``session_factory``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Protocol

from fantabot_app import paths

ENV_DATABASE_URL = "FANTABOT_DATABASE_URL"
DEFAULT_DB = "fantabot"


def _exported_url(environ: MutableMapping[str, str]) -> str | None:
    """The operator's own ``FANTABOT_DATABASE_URL``, or ``None`` when unset or blank.

    Read once, at construction, and never again: ``start()`` exports the same variable
    for the bundled server, so a later read could not tell the operator's instruction
    apart from our own echo of it.
    """
    return (environ.get(ENV_DATABASE_URL) or "").strip() or None


class _Server(Protocol):
    def get_uri(self, database: str | None = ..., driver: str | None = ...) -> str: ...
    def get_pid(self) -> int | None: ...
    def stop(self) -> None: ...


ServerFactory = Callable[[Path], _Server]
DbCreator = Callable[[str, str], None]


def _default_factory(pgdata: Path) -> _Server:
    """Start the bundled PostgreSQL 18 (imported lazily — the wheel is ~30 MB)."""
    from pixeltable_pgserver import get_server  # type: ignore[attr-defined]

    return get_server(pgdata)


def _default_create_db(admin_uri: str, dbname: str) -> None:
    """Create ``dbname`` if absent, over an AUTOCOMMIT connection to the admin database."""
    from sqlalchemy import create_engine, text

    engine = create_engine(admin_uri, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": dbname}
            ).scalar()
            if not exists:
                # dbname is our own constant, not user input; quote it defensively.
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()


class PostgresProvisioner:
    """Start/stop a per-user local Postgres and point fantabot at it.

    **An already-exported ``FANTABOT_DATABASE_URL`` wins.** In that mode this class starts
    no server, creates no database and never overwrites the variable; ``start()`` returns
    the exported URL, ``stop()`` is a no-op and ``status()`` reports ``external: True``
    with ``running: None`` — whether that database is up is not ours to know.

    The asymmetry is deliberate: an export is an explicit instruction from the operator,
    while a ``.env`` file found by whatever the working directory happens to be is the
    mechanism that put the CLI and the app on two different databases in the first place.
    So this reads ``os.environ`` and never ``.env``.
    """

    def __init__(
        self,
        *,
        pgdata: Path | None = None,
        dbname: str = DEFAULT_DB,
        server_factory: ServerFactory = _default_factory,
        create_db: DbCreator = _default_create_db,
        environ: MutableMapping[str, str] | None = None,
    ) -> None:
        self._pgdata = pgdata if pgdata is not None else paths.pgdata()
        self._dbname = dbname
        self._factory = server_factory
        self._create_db = create_db
        self._environ = environ if environ is not None else os.environ
        self._external = _exported_url(self._environ)
        self._server: _Server | None = None

    def start(self) -> str:
        """Start Postgres (idempotent), ensure the app db, export the URL, return it.

        A no-op returning the exported URL when the operator set one.
        """
        if self._external is not None:
            return self._external
        if self._server is None:
            self._pgdata.mkdir(parents=True, exist_ok=True)
            self._server = self._factory(self._pgdata)
        admin_uri = self._server.get_uri(database="postgres", driver="psycopg2")
        self._create_db(admin_uri, self._dbname)
        url = self.database_url()
        self._environ[ENV_DATABASE_URL] = url
        return url

    def database_url(self) -> str:
        if self._external is not None:
            return self._external
        if self._server is None:
            raise RuntimeError("Postgres is not started; call start() first")
        return self._server.get_uri(database=self._dbname, driver="psycopg2")

    def stop(self) -> None:
        if self._external is not None:
            return
        if self._server is not None:
            self._server.stop()
            self._server = None

    def status(self) -> dict[str, object]:
        """Report the server. ``running`` is ``None`` under an external URL — unknown."""
        if self._external is not None:
            return {"running": None, "pid": None, "url": self._external, "external": True}
        if self._server is None:
            return {"running": False, "pid": None, "url": None, "external": False}
        pid = self._server.get_pid()
        return {
            "running": pid is not None,
            "pid": pid,
            "url": self.database_url(),
            "external": False,
        }
