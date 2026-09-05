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
import re
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Literal, Protocol

from fantabot_app import paths

ENV_DATABASE_URL = "FANTABOT_DATABASE_URL"
DEFAULT_DB = "fantabot"

#: What `stop_bundled` did, for the CLI to render.
StopOutcome = Literal["stopped", "not_running", "not_provisioned"]


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


class _Postmaster(Protocol):
    """What ``pgdata/postmaster.pid`` says — pgserver's ``PostmasterInfo``, structurally."""

    pid: int

    def is_running(self) -> bool: ...
    def get_uri(
        self,
        user: str = ...,
        database: str | None = ...,
        driver: str | None = ...,
    ) -> str: ...


ServerFactory = Callable[[Path], _Server]
DbCreator = Callable[[str, str], None]
PostmasterReader = Callable[[Path], "_Postmaster | None"]
ServerStopper = Callable[[Path], None]

#: A database name we are willing to interpolate into ``CREATE DATABASE``.
_DBNAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def _validate_dbname(name: str) -> str:
    """Refuse anything that is not a plain lowercase identifier. See ``_default_create_db``."""
    if not _DBNAME.fullmatch(name):
        raise ValueError(
            f"not a database name: {name!r} "
            "(lowercase letters, digits and underscores; must not start with a digit)"
        )
    return name


def _default_factory(pgdata: Path, *, cleanup_mode: str | None = None) -> _Server:
    """Start the bundled PostgreSQL 18 (imported lazily — the wheel is ~30 MB).

    ``cleanup_mode=None`` is the load-bearing argument, and it is not pgserver's default
    (``'stop'``): with ``'stop'``, an ``atexit`` hook stops the server when the last
    process holding a handle exits, so ``fantabot-app db start`` would hand the operator a
    DSN to a server that died with the command that printed it. **The bundled server's
    lifetime is explicit** — only ``fantabot-app stop`` / ``db stop`` ever stops it.
    """
    from pixeltable_pgserver import get_server  # type: ignore[attr-defined]

    return get_server(pgdata, cleanup_mode=cleanup_mode)


def _default_postmaster(pgdata: Path) -> _Postmaster | None:
    """Read ``pgdata/postmaster.pid``: no server, no ``initdb``, no socket, no mkdir.

    This is the only way to answer "is it up, and at which DSN?" without starting one —
    ``get_server`` defaults to ``start=True`` and will happily ``initdb`` a whole cluster,
    and ``get_server(..., start=False).get_uri()`` asserts instead of answering.

    ``None`` means "not running": no pid file, a dead pid, or a file torn by a crash
    (pgserver asserts on its line count, so that surfaces as ``AssertionError``).
    """
    from pixeltable_pgserver.utils import PostmasterInfo

    try:
        info = PostmasterInfo.read_from_pgdata(pgdata)
    except (AssertionError, ValueError, OSError):
        return None
    if info is None or not info.is_running():
        return None
    return info


def _default_stop_server(pgdata: Path) -> None:
    """Stop a running bundled server, whichever process started it.

    ``get_server`` attaches to an already-running postmaster rather than starting a second
    one (``postgres_server.ensure_postgres_running`` takes its first branch), which is what
    populates the handle that ``stop()`` needs; a fresh ``PostgresProvisioner`` has none,
    because ``_server`` is set only by ``start()`` in its own process.

    Only ever called once the pid file says a server is up: on a stopped pgdata this call
    would *start* one in order to stop it.
    """
    from pixeltable_pgserver import get_server  # type: ignore[attr-defined]

    get_server(pgdata, cleanup_mode=None).stop()


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
                # `fantabot-app db create <name>` makes dbname user input, so it is
                # validated here as well as at the CLI boundary; the quoting is belt too.
                conn.execute(text(f'CREATE DATABASE "{_validate_dbname(dbname)}"'))
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
        postmaster: PostmasterReader = _default_postmaster,
        stop_server: ServerStopper = _default_stop_server,
        environ: MutableMapping[str, str] | None = None,
    ) -> None:
        self._pgdata = pgdata if pgdata is not None else paths.pgdata()
        self._dbname = dbname
        self._factory = server_factory
        self._create_db = create_db
        self._postmaster = postmaster
        self._stop_server = stop_server
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
        """Report the server. ``running`` is ``None`` under an external URL — unknown.

        ``provisioned`` and ``pgdata`` always describe the bundled data directory, which
        exists (or not) independently of an exported URL. With no handle of our own, the
        state comes from the pid file, so a server another process started is reported.
        """
        base: dict[str, object] = {
            "provisioned": self.is_provisioned(),
            "pgdata": str(self._pgdata),
        }
        if self._external is not None:
            return {**base, "running": None, "pid": None, "url": self._external, "external": True}
        if self._server is not None:
            pid = self._server.get_pid()
            return {
                **base,
                "running": pid is not None,
                "pid": pid,
                "url": self.database_url(),
                "external": False,
            }
        info = self._read_postmaster()
        if info is None:
            return {**base, "running": False, "pid": None, "url": None, "external": False}
        return {
            **base,
            "running": True,
            "pid": info.pid,
            "url": info.get_uri(database=self._dbname, driver="psycopg2"),
            "external": False,
        }

    # --- the bundled server, addressed by its data directory --------------------------
    #
    # `stop_bundled`, `bundled_url` and `create_database` deliberately ignore `_external`:
    # they are what `fantabot-app db ...` calls, and `db start` prints an `export` line the
    # operator pastes into the same shell. Were they to honour it, `db stop` would become a
    # silent no-op in exactly the shell `db start` just configured. `start()` and `stop()`
    # keep the T2.1 contract — they manage only what this process started.

    def is_provisioned(self) -> bool:
        """Has ``initdb`` ever run here? ``PG_VERSION`` is pgserver's own marker."""
        return (self._pgdata / "PG_VERSION").exists()

    def _read_postmaster(self) -> _Postmaster | None:
        return self._postmaster(self._pgdata) if self.is_provisioned() else None

    def stop_bundled(self) -> StopOutcome:
        """Stop the bundled server whoever started it. Idempotent; never starts one."""
        if not self.is_provisioned():
            return "not_provisioned"
        if self._read_postmaster() is None:
            return "not_running"
        self._stop_server(self._pgdata)
        self._server = None
        return "stopped"

    def bundled_url(self, database: str | None = None) -> str | None:
        """The bundled server's DSN, verbatim from the pid file. ``None`` when it is down.

        Verbatim matters twice over. The socket directory is only knowable from
        ``postmaster.pid`` — pgserver may put it under ``$TMPDIR`` rather than in pgdata —
        so a derived DSN would be a guess. And the path is **not** percent-encoded here:
        alembic's config is a ``ConfigParser``, which interpolates ``%`` and rejects a
        ``%2F``-encoded socket path outright.
        """
        info = self._read_postmaster()
        if info is None:
            return None
        return info.get_uri(database=database or self._dbname, driver="psycopg2")

    def create_database(self, name: str) -> None:
        """Create ``name`` on the bundled server if absent (``fantabot-app db create``)."""
        _validate_dbname(name)
        info = self._read_postmaster()
        if info is None:
            raise RuntimeError("the bundled Postgres is not running")
        self._create_db(info.get_uri(database="postgres", driver="psycopg2"), name)
