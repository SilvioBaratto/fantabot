"""Shared fixtures. Nothing here connects at import.

The ``db_session`` fixture is the foundation every ``-m db`` test uses. Three
things it has to get right:

* **Build the engine inside the fixture body.** A module-scope ``create_engine``
  would make the default, socket-free run construct one during collection.
* **Fail with an instruction.** A raw ``OperationalError`` traceback tells you
  the driver could not connect; it does not tell you the stack is down.
* **Leave nothing behind.** Each test runs inside a transaction that is rolled
  back, so tests cannot see each other's rows and a failed run does not poison
  the next one.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Generator
from typing import Any, NoReturn

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(autouse=True)
def _no_sockets(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The zero-sockets rule, enforced instead of asserted.

    CLAUDE.md calls it load-bearing and SPEC says it is "enforced by a test that
    fails if a socket is opened" — but until now only three *subprocess* checks
    covered it, one module's import each. Nothing watched the rest of the suite.
    This lands before any ``httpx`` code exists, so the first HTTP-client test is
    born measured rather than trusted.

    ``db``-marked nodes are exempt: reaching Postgres is their entire job.

    A subprocess spawns a fresh interpreter and this guard does not reach inside
    it — which is exactly why ``test_db_boundary``, ``test_state`` and
    ``test_lineup_guard`` each install their own in-process guard before
    importing. Those are not redundant with this one.

    ``monkeypatch`` rather than manual save/restore, so a test that raises cannot
    leave the guard installed for whatever runs next.
    """
    if request.node.get_closest_marker("db") is not None:
        return

    def blocked(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError(
            f"{request.node.nodeid} opened a socket. The default test tier must "
            "not touch the network — inject a fake, or mark the test `db`."
        )

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


# --- A plain, fixed-width console, decided before anything imports Rich -----
#
# Eight tests asserted on plain substrings of a command's output and passed
# everywhere except in a shell exporting ``FORCE_COLOR=3``, where Rich wrote
# escape codes *through* the words being matched — its number highlighter
# splits ``1,474`` into two coloured runs, so the substring is no longer in the
# string. The tests were right and the environment was leaking in.
#
# This runs at module scope rather than in a fixture because a ``Console``
# reads these variables in ``__init__`` and caches the answer, and ``cli.py``
# builds one at import. By the time any fixture body runs, the decision has
# been made. conftest is imported before the test modules that import the CLI,
# so here it is still early enough.
#
# Width matters for the same reason colour does: Rich lays out ``--help`` in a
# table sized to ``COLUMNS``, so a narrow window hyphenates an option name and
# a test looking for ``--league`` stops finding it.
for _forced in ("FORCE_COLOR", "CLICOLOR_FORCE", "TTY_COMPATIBLE", "CLICOLOR"):
    os.environ.pop(_forced, None)
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"
os.environ["COLUMNS"] = "200"


@pytest.fixture(scope="session")
def db_engine() -> Generator[Engine, None, None]:
    from fantabot.config import settings

    engine = create_engine(settings.fantabot_database_url, pool_pre_ping=True)

    detail: str | None = None
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        detail = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"

    # Raised outside the except block on purpose: raising inside it chains the
    # driver traceback onto the message, and "During handling of the above
    # exception" is exactly the noise pytrace=False is meant to remove.
    if detail is not None:
        engine.dispose()
        pytest.fail(
            "the database is not reachable — start it with: docker compose up -d\n"
            f"({detail})",
            pytrace=False,
        )

    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(db_engine: Engine) -> Generator[Connection, None, None]:
    """A connection with an outer transaction that is always rolled back."""
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


@pytest.fixture
def db_session(db_connection: Connection) -> Generator[Session, None, None]:
    """A session bound to the rolled-back connection.

    ``session.commit()`` inside a test commits to the *savepoint*, not to the
    database: the outer transaction still rolls back at teardown. Importers can
    therefore be exercised exactly as they run in production, and still leave
    the database as they found it.
    """
    factory = sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint")
    session = factory()
    try:
        yield session
    finally:
        session.close()
