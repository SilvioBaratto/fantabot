"""The database must stay behind ``fantabot.db``, and must not connect at import.

Two separate guarantees, easy to conflate:

* **No connect at import.** ``fantabot --help`` has to work with the compose
  stack down, and CLAUDE.md requires the default test run to open zero sockets.
  A module-scope ``create_engine`` would turn ``fantabot --help`` into a
  connection attempt and make the whole suite need Postgres just to collect.
* **No engine outside ``db/``.** The rule is about *ownership of connections*,
  not about the ``sqlalchemy`` name. Consumers legitimately annotate a
  ``Session`` parameter — under ``mypy --strict`` that means importing it — so
  asserting the string ``sqlalchemy`` appears nowhere would fail on code this
  plan goes on to write. What is banned is constructing an engine.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from _paths import PACKAGE

from fantabot.db.engine import DatabaseManager

PACKAGE = PACKAGE
FORBIDDEN = "create_engine"

#: The asta engine's decision layer. These modules hold every rule that decides what a
#: player is worth and which XI is legal, and they are testable precisely because none of
#: them can reach a database — the I/O lives in ``asta_engine/cli.py`` alone.
PURE_ASTA_MODULES = (
    "sentiment.py",
    "value.py",
    "optimizer.py",
    "legality.py",
    "roles.py",
    "state.py",
    "reservation.py",
)


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
        import sys

        def boom(*args, **kwargs):
            raise AssertionError("a connection was opened at import time")

        socket.socket.connect = boom
        socket.socket.connect_ex = boom
        socket.create_connection = boom

        import fantabot.cli

        # Checked here, BEFORE fantabot.db is imported below — otherwise this
        # test would be asserting against its own import.
        #
        # Newly true, and newly worth pinning: until the old auth module was
        # deleted, cli.py -> that module -> browser.py -> playwright.sync_api
        # ran at module scope, so importing the CLI loaded Playwright always.
        assert "playwright" not in sys.modules, "importing the CLI loaded Playwright"
        assert "sqlalchemy" not in sys.modules, "importing the CLI loaded SQLAlchemy"

        import fantabot.db

        assert fantabot.db.database_manager.engine is None, "engine built at import"
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_a_fresh_manager_has_no_engine_until_it_is_asked_for_a_session() -> None:
    """Asserted on a fresh instance rather than the module-level one: news fetch
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


def _imports(path: Path) -> set[str]:
    """Every module named by an import statement. Matches ``tests/test_aste_outage.py``."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("module", PURE_ASTA_MODULES)
def test_the_asta_decision_layer_cannot_reach_the_database(module: str) -> None:
    """Not "does not today" — cannot.

    Structural, for the same reason the collector's rule is: an assertion about the current
    text of these files holds for every future edit, where running the suite once with the
    stack down would only have proved it for one afternoon. It is also what keeps the
    default tier socket-free, since the whole value layer is reachable from ``fantabot.cli``.
    """
    names = _imports(PACKAGE / "asta_engine" / module)
    offenders = {n for n in names if n.startswith(("fantabot.db", "sqlalchemy"))}

    assert offenders == set(), f"{module} can reach the database via {offenders}"
