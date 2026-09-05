"""The harvest commands find their own files, from any working directory.

`--seed`, `--out`, the `landing` argument and `--listone` were required options or a
`data/aste_live/...` literal, which resolves only from the repository root. Both are the
same defect: the everyday invocation had to name four paths, and naming them wrong — or
running from the app's working directory, which is wherever its launcher was started —
addressed a landing zone that was not the one being collected into.

They now default to `config.harvest_dir()`, resolved **when the command runs**. A default
evaluated at import would bind the home of whichever process imported `typer` first, which
is exactly the property `bundled_pgdata` is a function for.

Explicit paths are untouched: a run that names its files is a run that meant to.
"""

from __future__ import annotations

import pathlib
import re
from pathlib import Path

import click
import pytest
import typer
from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """Rich wraps at the terminal width; a path assertion must not depend on where."""
    return ANSI.sub("", output).replace("\n", "")


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("FANTABOT_HARVEST_DIR", raising=False)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: tmp_path))
    return tmp_path


def _param(path: str, name: str) -> click.Parameter:
    """One parameter of one command, asked of Typer's Click tree.

    Introspection rather than `--help` parsing, for `test_cli_command_set.py`'s reason: a
    boxed help row is prose, and "is this option required" is a property, not a word that
    happens to appear in the box.
    """
    command: click.Command = typer.main.get_command(app)
    for step in path.split():
        found = command.get_command(click.Context(command), step)  # type: ignore[attr-defined]
        assert found is not None, f"no such command: {path}"
        command = found
    for param in command.params:
        if param.name == name:
            return param
    raise AssertionError(f"{path} has no parameter {name!r}")


class TestLoad:
    def test_it_looks_for_the_seed_under_the_harvest_home(self, home: Path) -> None:
        result = runner.invoke(app, ["harvest", "load"])

        assert result.exit_code == 2
        assert str(home / ".fantabot" / "aste_live" / "seed.json") in _plain(result.output)

    def test_an_explicit_seed_still_wins(self, home: Path, tmp_path: Path) -> None:
        elsewhere = tmp_path / "somewhere" / "seed.json"

        result = runner.invoke(app, ["harvest", "load", "--seed", str(elsewhere)])

        assert result.exit_code == 2
        assert str(elsewhere) in _plain(result.output)

    def test_the_landing_argument_is_optional(self) -> None:
        assert _param("harvest load", "landing").required is False

    def test_the_listone_bridge_is_optional(self) -> None:
        assert _param("harvest load", "listone").required is False


class TestCollect:
    def test_the_landing_zone_no_longer_has_to_be_named(self) -> None:
        """`--one` used to fail on a missing `--out` before it reached its own check."""
        result = runner.invoke(app, ["harvest", "collect", "--one", "abc"])

        assert "shard" in _plain(result.output).lower()

    def test_it_looks_for_the_seed_under_the_harvest_home(self, home: Path) -> None:
        result = runner.invoke(app, ["harvest", "collect"])

        assert result.exit_code == 2
        assert str(home / ".fantabot" / "aste_live" / "seed.json") in _plain(result.output)

    def test_one_auction_still_needs_no_seed(self, home: Path) -> None:
        """The home default must not turn `--one` into a seed run: `--seed` being unset is
        how this command is told there is a single auction to follow."""
        result = runner.invoke(app, ["harvest", "collect", "--one", "abc"])

        assert result.exit_code == 2
        assert "seed.json" not in _plain(result.output)


class TestScanAndBackfill:
    def test_scan_no_longer_demands_a_seed(self) -> None:
        assert _param("harvest scan", "seed").required is False

    def test_backfill_no_longer_demands_a_seed_or_a_bridge(self) -> None:
        assert _param("harvest backfill", "seed").required is False
        assert _param("harvest backfill", "listone").required is False

    def test_backfill_still_demands_the_recorded_log(self) -> None:
        """The one path that is genuinely per-run: an evening's recorded events, named
        by whoever recorded it. It has no home to default to."""
        assert _param("harvest backfill", "events").required is True


def test_the_listone_cache_follows_the_home(home: Path) -> None:
    from fantabot.adapters.http.fantalab import listone

    assert listone.default_cache() == home / ".fantabot" / "aste_live" / "listone_map.json"
