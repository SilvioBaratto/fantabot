"""`fantabot db-import` and the registry behind it.

The registry is empty until the first importer lands, which would make every
assertion here pass vacuously. So the tests that matter install a fake registry:
the ordering guarantee, the error message, and the dry-run short-circuit are
then exercised against something with content.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fantabot.cli import app
from fantabot.db import importers
from fantabot.db.importers import Importer, ImportResult

runner = CliRunner()


def _fake(name: str, source: str, rows: int) -> Importer:
    def load(session: Any, data_dir: Path) -> ImportResult:
        return ImportResult(table=name, inserted=rows)

    return Importer(name=name, sources=(source,), load=load, expected_rows=rows)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> tuple[Importer, ...]:
    """Dimensions first, then a fact table that points at them."""
    fake = (
        _fake("players", "quotazioni_classic.csv", 1474),
        _fake("teams", "quotazioni_classic.csv", 100),
        _fake("quotazioni", "quotazioni_classic.csv", 6402),
    )
    monkeypatch.setattr(importers, "REGISTRY", fake)
    return fake


class TestSelection:
    def test_naming_nothing_is_refused(self) -> None:
        """No bare `db-import`: naming the target is the safe-by-default posture."""
        result = runner.invoke(app, ["db-import"])
        assert result.exit_code != 0

    def test_all_and_table_together_are_refused(self, registry: Any) -> None:
        result = runner.invoke(app, ["db-import", "--all", "--table", "players"])
        assert result.exit_code != 0

    def test_an_unknown_table_names_every_valid_one(self, registry: Any) -> None:
        result = runner.invoke(app, ["db-import", "--table", "nonesuch"])

        assert result.exit_code != 0
        for name in ("players", "teams", "quotazioni"):
            assert name in result.output

    def test_resolve_preserves_registry_order_not_alphabetical(self, registry: Any) -> None:
        """Load order is dependency order. Sorting it is a foreign-key violation."""
        assert [imp.name for imp in importers.resolve(every=True, table=None)] == [
            "players",
            "teams",
            "quotazioni",
        ]

    def test_resolve_with_a_table_returns_just_that_one(self, registry: Any) -> None:
        selected = importers.resolve(every=False, table="teams")
        assert [imp.name for imp in selected] == ["teams"]


class TestDryRun:
    def test_prints_a_plan_and_writes_nothing(self, registry: Any) -> None:
        result = runner.invoke(app, ["db-import", "--all", "--dry-run"])

        assert result.exit_code == 0
        assert "players" in result.output
        assert "1,474" in result.output
        assert "nothing written" in result.output

    def test_opens_no_socket(self, monkeypatch: pytest.MonkeyPatch, registry: Any) -> None:
        """A dry run must short-circuit before any engine is constructed."""

        def boom(*args: object, **kwargs: object) -> None:
            raise AssertionError("--dry-run opened a connection")

        monkeypatch.setattr(socket.socket, "connect", boom)
        monkeypatch.setattr(socket, "create_connection", boom)

        assert runner.invoke(app, ["db-import", "--all", "--dry-run"]).exit_code == 0

    def test_a_missing_source_file_is_reported_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            importers, "REGISTRY", (_fake("ghost", "no_such_file.csv", 1),)
        )
        result = runner.invoke(app, ["db-import", "--all", "--dry-run"])

        assert result.exit_code == 0
        assert "missing" in result.output
        assert "no_such_file.csv" in result.output


def test_importing_the_registry_opens_no_socket() -> None:
    script = textwrap.dedent(
        """
        import socket

        def boom(*args, **kwargs):
            raise AssertionError("a connection was opened at import time")

        socket.socket.connect = boom
        socket.create_connection = boom

        from fantabot.db import importers
        assert importers.names() == [] or importers.names()
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
