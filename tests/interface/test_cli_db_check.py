"""`fantabot db check` — the operator view every later import phase reports through.

Kept in the default (socket-free) tier by injecting a session factory, so these
run without Postgres. The live-stack behaviour is covered by running the command
against the compose stack; what is pinned here is the part that is easy to get
wrong and impossible to notice: a dead database has to produce an instruction,
not a stack trace, and no credential may reach stdout on the way.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value

    def fetchone(self) -> Any:
        return (self._value,)


class _Session:
    """Answers every probe: the table exists, holds 7 rows, occupies 64 kB."""

    def __init__(self) -> None:
        self._answers = [1]

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        if "information_schema" in sql:
            return _Result(True)
        if "count(*)" in sql:
            return _Result(7)
        if "pg_size_pretty" in sql:
            return _Result("64 kB")
        if "pg_total_relation_size" in sql:
            return _Result(65536)
        return _Result(1)

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


def _use_fake_session(monkeypatch: Any) -> None:
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(database_manager, "_session_factory", _Session)


def test_reports_health_latency_and_a_row_per_table(monkeypatch: Any) -> None:
    _use_fake_session(monkeypatch)

    result = runner.invoke(app, ["db", "check"])

    assert result.exit_code == 0
    assert "health" in result.output
    assert "ms" in result.output
    assert "players" in result.output
    assert "7" in result.output


def test_an_unreachable_database_exits_nonzero_with_an_instruction(monkeypatch: Any) -> None:
    class _Dead:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def commit(self) -> None: ...

        def rollback(self) -> None: ...

        def close(self) -> None: ...

    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(database_manager, "_session_factory", _Dead)

    result = runner.invoke(app, ["db", "check"])

    assert result.exit_code != 0
    assert "docker compose up -d" in result.output
    assert "Traceback" not in result.output


def test_the_dsn_password_is_not_printed_when_the_database_is_down(monkeypatch: Any) -> None:
    from fantabot import config
    from fantabot.adapters.persistence import database_manager

    monkeypatch.setattr(
        config.settings,
        "fantabot_database_url",
        "postgresql+psycopg2://u:DbCheckCanary@localhost:54321/fantabot",
    )

    class _Dead:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def commit(self) -> None: ...

        def rollback(self) -> None: ...

        def close(self) -> None: ...

    monkeypatch.setattr(database_manager, "_session_factory", _Dead)

    result = runner.invoke(app, ["db", "check"])

    assert "DbCheckCanary" not in result.output


def test_it_does_not_need_league_credentials(monkeypatch: Any) -> None:
    """db check must work before `fantabot auth login` has ever been run."""
    from fantabot import config

    _use_fake_session(monkeypatch)
    monkeypatch.setattr(config.settings, "lega_email", "")
    monkeypatch.setattr(config.settings, "lega_password", "")
    monkeypatch.setattr(config.settings, "lega_url", "")

    assert runner.invoke(app, ["db", "check"]).exit_code == 0
