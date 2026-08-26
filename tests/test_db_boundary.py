"""The database must stay behind ``fantabot.db``, and must not connect at import.

Two separate guarantees, easy to conflate:

* **No connect at import.** ``fantabot auth`` has to work with the compose stack
  down, and CLAUDE.md requires the default test run to open zero sockets. A
  module-scope ``create_engine`` would turn ``fantabot --help`` into a
  connection attempt and make all 156 tests need Postgres just to collect.
* **No engine outside ``db/``.** The rule is about *ownership of connections*,
  not about the ``sqlalchemy`` name. Consumers legitimately annotate a
  ``Session`` parameter — under ``mypy --strict`` that means importing it — so
  asserting the string ``sqlalchemy`` appears nowhere would fail on code this
  plan goes on to write. What is banned is constructing an engine.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from fantabot.db.engine import DatabaseManager

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "fantabot"
FORBIDDEN = "create_engine"


def test_no_engine_is_constructed_outside_the_db_package() -> None:
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.relative_to(PACKAGE).parts[0] != "db" and FORBIDDEN in path.read_text()
    ]
    assert offenders == [], (
        f"{FORBIDDEN} belongs in fantabot/db/engine.py only; found in: {offenders}"
    )


def test_importing_the_cli_opens_no_connection() -> None:
    """Runs in a fresh interpreter so it measures import, not test-order luck."""
    script = textwrap.dedent(
        """
        import socket

        def boom(*args, **kwargs):
            raise AssertionError("a connection was opened at import time")

        socket.socket.connect = boom
        socket.socket.connect_ex = boom
        socket.create_connection = boom

        import fantabot.cli
        import fantabot.db

        assert fantabot.db.database_manager.engine is None, "engine built at import"
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_a_fresh_manager_has_no_engine_until_it_is_asked_for_a_session() -> None:
    """Asserted on a fresh instance rather than the module-level one: news-fetch
    connects now, so whether the global has an engine depends on test order.
    The import-time guarantee is covered by the subprocess test above."""
    assert DatabaseManager().engine is None


class _FakeSession:
    """Records the calls get_session is contractually required to make."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def commit(self) -> None:
        self.calls.append("commit")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.calls.append("close")


def _manager_with(fake: _FakeSession) -> DatabaseManager:
    """A sessionmaker stand-in: DatabaseManager only ever calls it with no args."""

    def factory() -> _FakeSession:
        return fake

    return DatabaseManager(session_factory=factory)


def test_get_session_commits_on_a_clean_exit() -> None:
    fake = _FakeSession()
    with _manager_with(fake).get_session():
        pass

    assert fake.calls == ["commit", "close"]


def test_get_session_rolls_back_and_re_raises_on_error() -> None:
    fake = _FakeSession()
    manager = _manager_with(fake)

    try:
        with manager.get_session():
            raise ValueError("boom")
    except ValueError:
        pass
    else:  # pragma: no cover - the raise above must propagate
        raise AssertionError("get_session swallowed the exception")

    assert fake.calls == ["rollback", "close"]


def test_an_injected_factory_means_no_engine_is_ever_built() -> None:
    """The importer and repository suites depend on this: a fake factory keeps
    the whole default tier socket-free."""
    manager = _manager_with(_FakeSession())
    with manager.get_session():
        pass

    assert manager.engine is None
