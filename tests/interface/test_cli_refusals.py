"""The two sentences every command body used to carry for itself. **Zero sockets.**

`interface/refusals.py` holds them now: `resolve_league` was verbatim in `lineup.py` and
`lega.py` and inlined a third time in `app.py`, and `database_unreachable` was twelve copies
of the same two statements, in eleven functions behind ten commands, across three modules.
Both carry an **exit code**, which is what a cron wrapper reads and what a second copy is
free to disagree about.

Nothing covered any of the twenty-one paths below before the collapse — this file is the
characterization capture that proved it byte-for-byte, kept because the value of collapsing
twelve copies is lost the moment a thirteenth is written by hand. So the scans at the end
are half of the point: they fail when a copy grows back, which a per-command assertion
cannot see.

The database is faked by a session factory that raises `OperationalError` on construction,
so every case reaches the handler through the real Typer parsing, the real command body and
the real `with database_manager.get_session()`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from _paths import PACKAGE
from cryptography.fernet import Fernet
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()

OUTAGE = "database unreachable: OperationalError\n"
NO_LEGA = "no lega id: pass --league or set FANTABOT_LEAGUE_ID\n"


class _Session:
    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def _raise() -> _Session:
    raise OperationalError("SELECT 1", {}, Exception("no socket"))


def _after(n: int) -> Any:
    """Hand out `n` working sessions, then go down.

    `lega sync --write` reads in one transaction and writes in another — deliberately, so a
    multi-megabyte GET is not held open across a write — so its two handlers are two
    different failures and only the second needs a live read behind it.
    """
    state = {"calls": 0}

    def _make() -> _Session:
        state["calls"] += 1
        if state["calls"] <= n:
            return _Session()
        return _raise()

    return _make


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A reachable lega and a usable key, so nothing refuses before the database is asked."""
    from fantabot import config
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(
        config.settings, "fantabot_encryption_key", Fernet.generate_key().decode()
    )
    # Pinned rather than read: the operator's real lega lives in a gitignored `.env`, absent
    # in CI, and an unset one is the *other* refusal this file tests.
    monkeypatch.setattr(config.settings, "fantabot_league_id", 4103937)
    monkeypatch.setattr(database_manager, "_session_factory", _raise)
    return SimpleNamespace(monkeypatch=monkeypatch, manager=database_manager)


# -- every command that reports a database outage ----------------------------------------

OUTAGE_CASES: list[tuple[str, list[str]]] = [
    ("lega sync (the read phase)", ["lega", "sync", "--league", "1"]),
    ("lega show", ["lega", "show", "--league", "1"]),
    ("lineup show", ["lineup", "show", "--league", "1", "--competition", "2"]),
    (
        "lineup plan (indexcompare)",
        ["lineup", "plan", "--league", "1", "--competition", "2", "--model", "indexcompare"],
    ),
    (
        "lineup plan (projection)",
        ["lineup", "plan", "--league", "1", "--competition", "2", "--model", "projection"],
    ),
    ("lineup submit", ["lineup", "submit", "--league", "1", "--competition", "2"]),
    ("lineup refresh", ["lineup", "refresh", "--league", "1", "--cmday", "5"]),
    (
        "lineup backtest",
        ["lineup", "backtest", "--league", "1", "--sub-mode", "basic", "--rooms", "1"],
    ),
    # `--sub-mode` stated: the command refuses without one, and `conftest.py` clears
    # `FANTABOT_LINEUP_SUB_MODE` so the operator's `.env` cannot supply it here.
    (
        "lineup shadow-report",
        ["lineup", "shadow-report", "--league", "1", "--sub-mode", "basic"],
    ),
    ("db backfill-teams", ["db", "backfill-teams"]),
    ("db snapshot-team", ["db", "snapshot-team", "--league", "1"]),
]


@pytest.mark.parametrize(
    "argv", [c[1] for c in OUTAGE_CASES], ids=[c[0] for c in OUTAGE_CASES]
)
def test_an_outage_names_the_type_and_exits_one(cli: Any, argv: list[str]) -> None:
    """The type, a fixed sentence, and code 1 — and never `str(exc)`.

    Equality, not `in`: the defect this guards against is a driver's message reaching the
    terminal, and `str(OperationalError)` carries the statement and can carry the DSN.
    """
    result = runner.invoke(app, argv)

    assert result.output == OUTAGE
    assert result.exit_code == 1


def test_lega_sync_reports_the_write_phase_the_same_way(cli: Any) -> None:
    """The twelfth site: reads done, writes refused. It is a separate handler."""
    from fantabot.application import lega_sync

    cli.monkeypatch.setattr(cli.manager, "_session_factory", _after(1))
    cli.monkeypatch.setattr(
        lega_sync,
        "collect",
        lambda *a, **k: SimpleNamespace(rosters=[], failures=[], ok=True),
    )

    result = runner.invoke(app, ["lega", "sync", "--league", "1", "--write"])

    assert result.output == OUTAGE
    assert result.exit_code == 1


# -- every command that needs a lega and cannot find one ---------------------------------

NO_LEGA_CASES: list[tuple[str, list[str]]] = [
    ("lega sync", ["lega", "sync"]),
    ("lega show", ["lega", "show"]),
    ("lineup show", ["lineup", "show", "--competition", "2"]),
    ("lineup plan", ["lineup", "plan", "--competition", "2"]),
    ("lineup submit", ["lineup", "submit", "--competition", "2"]),
    ("lineup refresh", ["lineup", "refresh", "--cmday", "5"]),
    ("lineup backtest", ["lineup", "backtest", "--sub-mode", "basic"]),
    ("lineup shadow-report", ["lineup", "shadow-report"]),
    ("db snapshot-team", ["db", "snapshot-team"]),
]


@pytest.mark.parametrize(
    "argv", [c[1] for c in NO_LEGA_CASES], ids=[c[0] for c in NO_LEGA_CASES]
)
def test_no_lega_refuses_before_anything_is_opened(cli: Any, argv: list[str]) -> None:
    """One sentence, code 1, and the database never asked — the factory here always raises,
    so an outage line in the output would mean the refusal ran too late."""
    from fantabot import config

    cli.monkeypatch.setattr(config.settings, "fantabot_league_id", 0)

    result = runner.invoke(app, argv)

    assert result.output == NO_LEGA
    assert result.exit_code == 1


# -- and they stay collapsed -------------------------------------------------------------


def _modules_naming(fragment: str) -> set[str]:
    return {
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if fragment in path.read_text(encoding="utf-8")
    }


def test_the_outage_sentence_is_written_in_two_places_and_both_are_deliberate() -> None:
    """`interface/harvest.py` is the exception and says so in `refusals.py`'s docstring: it
    opens with the same line, then says two more things, and under `--follow` it *retries*
    rather than exiting. Same sentence, different decision."""
    assert _modules_naming("database unreachable: {type(exc).__name__}") == {
        "interface/refusals.py",
        "interface/harvest.py",
    }


def test_the_no_lega_sentence_is_written_once() -> None:
    """The app's `schedule.py` refuses an install in its own words — it raises
    `ScheduleRefused`, reads no setting and adds a sentence about the plist — and lives
    outside this package, which is why the scan is scoped to it."""
    assert _modules_naming("no lega id: pass --league") == {"interface/refusals.py"}


def test_no_season_default_is_spelled_out_a_second_time() -> None:
    """`options.SEASON` is every `--season` default in the CLI. Three commands spelled the
    literal out in their own signature, which is three places for next July to be missed."""
    naming = _modules_naming('"2026/27"') | _modules_naming("'2026/27'")

    assert "interface/options.py" in naming
    assert not {name for name in naming if name.startswith("interface/")} - {
        "interface/options.py"
    }


def test_the_options_docstring_counts_the_aliases_it_holds() -> None:
    """The count in prose and the aliases in the file, compared — because this repo has
    already spent a day repairing a docstring that described a different number of things
    than the module held."""
    import typing

    from fantabot.interface import options

    written = {
        name
        for name, value in vars(options).items()
        if not name.startswith("_") and typing.get_origin(value) is typing.Annotated
    }
    head = Path(options.__file__).read_text(encoding="utf-8").split("\n", 2)[2]

    assert len(written) == 13
    assert "Thirteen aliases" in head
