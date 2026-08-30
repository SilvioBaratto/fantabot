"""`fantabot harvest backfill`, exercised without a database.

`--dry-run` exists so the expensive half can be checked in the default tier:
building 144,518 rows is where the mistakes are, and it needs no connection. A
command whose only verifiable path requires Postgres is a command nobody runs
before committing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _paths import ONE_AUCTION
from typer.testing import CliRunner

from fantabot.cli import app

runner = CliRunner()

#: Rich styles option names, and it does not keep them contiguous: `--seed` is
#: rendered as an escaped `-` followed by an escaped `-seed`, so the literal
#: string never appears in the output. Asserting on raw output is the same trap
#: that has eight other CLI tests in this repo red.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    return ANSI.sub("", output)

STATES = ONE_AUCTION


def _seed(tmp_path: Path) -> Path:
    auction_id = json.loads(STATES.read_text().splitlines()[0])["auction_id"]
    path = tmp_path / "seed.json"
    path.write_text(
        json.dumps([[auction_id, "15", 10, 500, 25, 25, "random", "free", 7, 7, "FIXTURE"]])
    )
    return path


def test_the_command_is_registered_with_its_flags() -> None:
    result = runner.invoke(app, ["harvest", "backfill", "--help"])
    assert result.exit_code == 0
    for flag in ("--seed", "--listone", "--asta-type", "--dry-run"):
        assert flag in _plain(result.output), f"{flag} is missing from the help"


def test_a_dry_run_reports_counts_and_opens_nothing(tmp_path: Path) -> None:
    """No database, no network — the autouse socket guard would fail this test
    outright if the dry run reached for either."""
    result = runner.invoke(
        app,
        ["harvest", "backfill", str(STATES), "--seed", str(_seed(tmp_path)), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "328" in _plain(result.output), "the event count is not reported"
    assert "18" in _plain(result.output), "the assignment count is not reported"


def test_a_missing_seed_exits_two_with_an_instruction(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["harvest", "backfill", str(STATES), "--seed", str(tmp_path / "absent.json"), "--dry-run"],
    )
    assert result.exit_code == 2
    assert "absent.json" in _plain(result.output)


def test_an_unknown_format_is_refused_before_any_work(tmp_path: Path) -> None:
    """`asta_type` is NOT NULL and only two values are real. Catching a typo here
    beats a constraint violation after building 144,518 rows."""
    result = runner.invoke(
        app,
        [
            "harvest", "backfill", str(STATES),
            "--seed", str(_seed(tmp_path)),
            "--asta-type", "manta",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "manta" in _plain(result.output)
