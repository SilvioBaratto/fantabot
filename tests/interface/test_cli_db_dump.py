"""`fantabot db dump` after the compose stack was deleted.

The dump used to shell `docker compose exec -T db pg_dump`, which addressed the container
by service name. With no compose stack there is no container, so the dump runs `pg_dump`
directly against the configured DSN — and the DSN is the bundled server's unix socket,
which `pg_dump` accepts as a libpq connection URI once the SQLAlchemy driver suffix is
gone (`postgresql+psycopg2://` is not a scheme libpq knows).

The argv is a pure function of the DSN, so it is pinned here without running anything.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from fantabot.interface.app import _pg_dump_argv, app

runner = CliRunner()

SOCKET = "postgresql+psycopg2://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"


def test_the_driver_suffix_is_stripped_for_libpq() -> None:
    """`postgresql+psycopg2://` is SQLAlchemy's spelling; libpq rejects it."""
    argv = _pg_dump_argv(SOCKET)

    assert argv[0] == "pg_dump"
    assert argv[-1] == "postgresql://postgres:@/fantabot?host=/Users/x/.fantabot/pgdata"
    assert "+psycopg2" not in " ".join(argv)


def test_the_dump_stays_in_the_custom_format() -> None:
    """`-Fc` is what makes pg_restore selective; a plain SQL dump is not the same artefact."""
    assert "-Fc" in _pg_dump_argv(SOCKET)


def test_a_tcp_dsn_survives_unchanged_apart_from_the_driver() -> None:
    argv = _pg_dump_argv("postgresql+psycopg2://postgres:pw@localhost:5432/fantabot")

    assert argv[-1] == "postgresql://postgres:pw@localhost:5432/fantabot"


def test_no_container_is_addressed() -> None:
    """The regression this file exists for: `docker compose exec -T db` named a service."""
    assert "docker" not in _pg_dump_argv(SOCKET)


# -- the Typer body -----------------------------------------------------------------------
#
# `_pg_dump_argv` above is a pure function and was the only thing here. The command that
# calls it was measured 2026-09-24 at **0 of 14 body statements**, one of five commands no
# test entered at all — so the three decisions the body makes for itself were unpinned:
# where the file goes, which calendar names it, and that neither failure is silent.


@pytest.fixture
def dump(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Both application calls recorded, neither run. No `pg_dump`, no database."""
    from fantabot.application import db_dump
    from fantabot.config import settings

    seen: dict[str, Any] = {"target": None, "dump": None, "refuse": None, "fail": None}

    def _target(home: Path, today: Any) -> Path:
        seen["target"] = (home, today)
        if seen["refuse"] is not None:
            raise seen["refuse"]
        return tmp_path / f"fantabot-db-{today:%Y%m%d}.dump"

    def _run(target: Path, database_url: str, **_kw: Any) -> Any:
        seen["dump"] = (target, database_url)
        if seen["fail"] is not None:
            raise seen["fail"]
        return db_dump.DumpWrote(path=target, size_bytes=419_430_400)

    monkeypatch.setattr(db_dump, "dump_target", _target)
    monkeypatch.setattr(db_dump, "run_dump", _run)
    monkeypatch.setattr(settings, "fantabot_database_url", "postgresql+psycopg2:///fantabot")
    return seen


def test_the_dump_is_aimed_at_home_never_the_working_directory(dump: dict[str, Any]) -> None:
    """It holds the `league_tokens` rows — encrypted, but still credentials — and anything
    inside the working tree is one `git add -A` from a public commit."""
    result = runner.invoke(app, ["db", "dump"])

    assert result.exit_code == 0
    assert dump["target"][0] == Path.home()
    assert dump["target"][0] != Path.cwd()


def test_the_filename_is_stamped_in_utc_not_local_time(
    dump: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The filename is the only thing separating two dumps, so a machine that travels
    would otherwise write today's over yesterday's.

    The zone is chosen from the current UTC hour so that the local date is *guaranteed*
    to differ — otherwise this test would agree with a local-time reading for most of the
    day and only go red on the hours nobody runs it.
    """
    now = datetime.now(UTC)
    monkeypatch.setenv("TZ", "Etc/GMT-14" if now.hour >= 11 else "Etc/GMT+11")
    time.tzset()
    try:
        assert datetime.now().date() != now.date(), "the zone did not move the date"

        result = runner.invoke(app, ["db", "dump"])

        assert result.exit_code == 0
        assert dump["target"][1] == datetime.now(UTC).date()
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_a_refused_target_exits_1_and_never_runs_pg_dump(dump: dict[str, Any]) -> None:
    """The external-volume refusal. Running the dump anyway would put 400 MB on the drive
    whose unmounting is the failure this command exists for."""
    from fantabot.application.db_dump import DumpRefused

    dump["refuse"] = DumpRefused("refusing to write the dump onto an external volume")

    result = runner.invoke(app, ["db", "dump"])

    assert result.exit_code == 1
    assert "external volume" in result.output
    assert dump["dump"] is None


@pytest.mark.parametrize("failure", ["PgDumpMissing", "PgDumpFailed"])
def test_a_failed_dump_exits_1(dump: dict[str, Any], failure: str) -> None:
    """Both are exit 1. A dump that did not happen and reports 0 is the worst outcome
    available: the operator believes there is a backup."""
    from fantabot.application import db_dump as module

    dump["fail"] = getattr(module, failure)("pg_dump is not on PATH")

    result = runner.invoke(app, ["db", "dump"])

    assert result.exit_code == 1
    assert "pg_dump is not on PATH" in result.output


def test_a_written_dump_reports_its_size_and_how_to_restore(dump: dict[str, Any]) -> None:
    result = runner.invoke(app, ["db", "dump"])

    assert result.exit_code == 0
    assert "400 MB" in result.output
    assert "restore" in result.output
    assert dump["dump"][1] == "postgresql+psycopg2:///fantabot"


def test_no_credential_reaches_the_output(
    dump: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DSN is handed to `run_dump` and must not also be printed: `db dump` is a
    command an operator runs with someone looking over their shoulder."""
    from fantabot.config import settings

    monkeypatch.setattr(
        settings, "fantabot_database_url", "postgresql+psycopg2://u:S3cr3tCanary@h/fantabot"
    )

    result = runner.invoke(app, ["db", "dump"])

    assert result.exit_code == 0
    assert "S3cr3tCanary" not in result.output
