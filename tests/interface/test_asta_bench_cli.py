"""`fantabot asta bench` end to end, through the real CLI. No database, no socket.

Unlike every other `asta` command this one needs no `pinned_world()` — it reads only the
golden fixture files under its `--replay` argument and that directory's parent, exactly as
`tests/application/test_asta_bench.py` does directly. If it ever grew a database or network
read, this test would fail under the default tier's socket block (`conftest.py`) without any
patching to give it away.
"""

from __future__ import annotations

from _paths import GOLDEN
from typer.testing import CliRunner

from fantabot.interface.app import app


def test_it_reports_all_three_scenarios_passing_and_exits_zero() -> None:
    result = CliRunner().invoke(
        app, ["asta", "bench", "--replay", str(GOLDEN / "asta_2026_09_01")]
    )

    assert result.exit_code == 0, result.output
    assert "Vicario: PASS" in result.output
    assert "Ostigard: PASS" in result.output
    assert "Malen: PASS" in result.output


def test_a_missing_fixture_directory_fails_loudly_not_silently() -> None:
    result = CliRunner().invoke(
        app, ["asta", "bench", "--replay", str(GOLDEN / "does_not_exist")]
    )

    assert result.exit_code != 0
