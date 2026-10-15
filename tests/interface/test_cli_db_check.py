"""`fantabot db check` — the operator view every later import phase reports through.

Kept in the default (socket-free) tier by injecting a session factory, so these
run without Postgres. The live-stack behaviour is covered by running the command
against the compose stack; what is pinned here is the part that is easy to get
wrong and impossible to notice: a dead database has to produce an instruction,
not a stack trace, and no credential may reach stdout on the way.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from fantabot.interface.app import app

runner = CliRunner()


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value

    def scalars(self) -> _Result:
        return self

    def all(self) -> Any:
        return self._value if isinstance(self._value, list) else []

    def fetchone(self) -> Any:
        return (self._value,)


def _the_code_head() -> str:
    """This checkout's own head, so the default session is *at head* whatever it is today."""
    from fantabot.adapters.persistence.alembic_scripts import load_script_revisions

    scripts = load_script_revisions()
    assert scripts is not None
    return scripts.heads[0]


class _Session:
    """Answers every probe: the table exists, holds 7 rows, occupies 64 kB.

    `alembic_version` answers as the class attribute says: a list of revisions, or `None`
    for a database with no such table.
    """

    versions: ClassVar[list[str] | None] = None
    tables: ClassVar[bool] = True

    def __init__(self) -> None:
        self._answers = [1]

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        if "information_schema" in sql:
            if params and params.get("name") == "alembic_version":
                return _Result(self.versions is not None)
            return _Result(self.tables)
        if "version_num" in sql:
            return _Result(self.versions)
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


def _use_fake_session(
    monkeypatch: Any, versions: list[str] | str | None = "head", *, tables: bool = True
) -> None:
    """The fake session, holding `versions` in `alembic_version` (`"head"` for the code's).

    `tables` is whether the application's own tables exist; `False` is a fresh database.
    """
    from fantabot.adapters.persistence import database_manager

    held = [_the_code_head()] if versions == "head" else versions

    class _Configured(_Session):
        pass

    _Configured.versions = held  # type: ignore[assignment]
    _Configured.tables = tables
    monkeypatch.setattr(database_manager, "_session_factory", _Configured)


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
    assert "fantabot-app db start" in result.output
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
    """db check must work before `fantabot auth login` has ever been run.

    Audited 2026-09-24 as vacuous — "three settings monkeypatched to their declared
    defaults, two of which no code reads" — and **re-measured as the opposite**. The
    defaults are declared `""`, but the operator's `.env` sets `lega_url` to a real value,
    so patching it to `""` is the one thing in this file that puts `db check` in the
    before-first-login state. With a `if not settings.lega_url: raise typer.Exit(2)`
    planted in `db_check`, this test was the **only** one of the four here that reddened;
    the other three inherit the configured value and stayed green. It is the sole guard on
    the claim its name makes, and it is the `lega_url` patch — not the two inert ones —
    that carries it.
    """
    from fantabot import config

    _use_fake_session(monkeypatch)
    monkeypatch.setattr(config.settings, "lega_email", "")
    monkeypatch.setattr(config.settings, "lega_password", "")
    monkeypatch.setattr(config.settings, "lega_url", "")

    assert runner.invoke(app, ["db", "check"]).exit_code == 0


class TestTheSchemaLine:
    """`db check` names the schema's revision against the code's head.

    The table listing reports declared-but-missing *tables*, so a migration that only drops
    columns leaves a clean screen over a database whose every write fails; the revision
    comparison is the only thing that sees it.
    """

    def test_a_database_at_head_says_so(self, monkeypatch: Any) -> None:
        """The healthy case prints one quiet line naming the revision."""
        _use_fake_session(monkeypatch)

        result = runner.invoke(app, ["db", "check"])

        assert result.exit_code == 0
        assert f"schema: at head {_the_code_head()}" in result.output
        assert "alembic upgrade head" not in result.output

    def test_a_database_behind_the_code_names_both_revisions_and_what_to_do(
        self, monkeypatch: Any
    ) -> None:
        """Dump first, then upgrade: a migration can be destructive and the operator decides."""
        _use_fake_session(monkeypatch, ["e35dbc3294a7"])

        result = runner.invoke(app, ["db", "check"])

        assert "e35dbc3294a7" in result.output
        assert _the_code_head() in result.output
        assert "behind" in result.output
        assert "fantabot db dump" in result.output
        assert result.output.index("fantabot db dump") < result.output.index("alembic upgrade head")

    def test_behind_keeps_the_exit_code_zero(self, monkeypatch: Any) -> None:
        """Same class as the missing-tables warning, which exits 0: the restore recipe ends in
        `fantabot db check` against a freshly restored database, and a nonzero exit there
        would turn a documented step into a failure."""
        _use_fake_session(monkeypatch, ["e35dbc3294a7"])

        assert runner.invoke(app, ["db", "check"]).exit_code == 0

    def test_an_unversioned_database_is_named_and_not_told_to_upgrade_blind(
        self, monkeypatch: Any
    ) -> None:
        """No `alembic_version` table: `upgrade head` would try to create what exists."""
        _use_fake_session(monkeypatch, None)

        result = runner.invoke(app, ["db", "check"])

        assert "unversioned" in result.output
        assert "alembic upgrade head" not in result.output
        assert "fantabot db dump" in result.output

    def test_a_revision_the_scripts_do_not_know_is_named(self, monkeypatch: Any) -> None:
        """A database migrated by a checkout with migrations this one lacks."""
        _use_fake_session(monkeypatch, ["ffffffffffff"])

        result = runner.invoke(app, ["db", "check"])

        assert "ffffffffffff" in result.output
        assert "not in this checkout" in result.output

    def test_an_install_with_no_alembic_scripts_says_it_cannot_compare(
        self, monkeypatch: Any
    ) -> None:
        """A wheel has no `alembic.ini` beside it; the database half is still reported."""
        from fantabot.application import schema_check

        _use_fake_session(monkeypatch)
        monkeypatch.setattr(schema_check, "load_script_revisions", lambda: None)

        result = runner.invoke(app, ["db", "check"])

        assert result.exit_code == 0
        assert "no alembic scripts" in result.output
        assert _the_code_head() in result.output

    def test_a_schema_read_that_fails_does_not_cost_the_rest_of_the_screen(
        self, monkeypatch: Any
    ) -> None:
        """The table listing is what the command is for; the schema line is an addition."""
        from sqlalchemy.exc import OperationalError

        class _Flaky(_Session):
            versions: ClassVar[list[str] | None] = [_the_code_head()]

            def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
                if "version_num" in str(statement):
                    raise OperationalError("SELECT", {}, Exception("terminating connection"))
                return super().execute(statement, params)

        from fantabot.adapters.persistence import database_manager

        monkeypatch.setattr(database_manager, "_session_factory", _Flaky)

        result = runner.invoke(app, ["db", "check"])

        assert result.exit_code == 0
        assert "could not be checked" in result.output
        assert "players" in result.output

    def test_a_fresh_database_is_told_to_create_the_schema_and_not_to_dump_it(
        self, monkeypatch: Any
    ) -> None:
        """No tables and no `alembic_version`: the line below the table already says
        `alembic upgrade head`, and the schema line must not contradict it with a dump of
        nothing and a comparison against the models."""
        _use_fake_session(monkeypatch, None, tables=False)

        result = runner.invoke(app, ["db", "check"])

        assert result.exit_code == 0
        assert "empty" in result.output
        assert "Create the schema with `alembic upgrade head`" in result.output
        assert "fantabot db dump" not in result.output
        assert "compare against the models" not in result.output
        assert "unversioned" not in result.output

    def test_tables_with_no_version_table_are_still_unversioned_not_empty(
        self, monkeypatch: Any
    ) -> None:
        """The same missing table, on a populated database, is the case that must not be told
        to upgrade."""
        _use_fake_session(monkeypatch, None, tables=True)

        result = runner.invoke(app, ["db", "check"])

        assert "unversioned" in result.output
        assert "Create the schema" not in result.output

    def test_a_migration_that_will_not_load_does_not_cost_the_rest_of_the_screen(
        self, monkeypatch: Any
    ) -> None:
        """Reading the scripts executes every migration file. A half-written one raises
        `ImportError`, not alembic's own error, and an unhandled one would abort the command
        with nothing printed — health and the table listing are the point of the screen."""
        from fantabot.application import schema_check

        def _broken() -> Any:
            raise ImportError("boom")

        _use_fake_session(monkeypatch)
        monkeypatch.setattr(schema_check, "load_script_revisions", _broken)

        result = runner.invoke(app, ["db", "check"])

        assert result.exit_code == 0
        assert "could not be checked (ImportError)" in result.output
        assert "health: ok" in result.output
        assert "players" in result.output


class TestTheSchemaColour:
    """Severity is colour on this screen. CliRunner's console is monochrome, so these swap
    in one that emits ANSI and look at the codes around the `schema:` line."""

    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"

    def _colour_of_schema_line(self, monkeypatch: Any) -> tuple[str, int]:
        import io

        from rich.console import Console

        from fantabot.interface import app as app_module

        buffer = io.StringIO()
        monkeypatch.setattr(
            app_module,
            "console",
            Console(
                file=buffer,
                force_terminal=True,
                color_system="standard",
                width=300,
                no_color=False,
                highlight=False,
            ),
        )
        result = runner.invoke(app, ["db", "check"])
        line = next(row for row in buffer.getvalue().splitlines() if row.startswith("schema:"))
        return line, result.exit_code

    def test_a_schema_behind_the_code_is_red_and_still_exits_zero(self, monkeypatch: Any) -> None:
        """It is the state in which every write to a dropped column fails, so it is not a
        caution. The exit stays 0: the documented restore recipe ends in this command."""
        _use_fake_session(monkeypatch, ["e35dbc3294a7"])

        line, exit_code = self._colour_of_schema_line(monkeypatch)

        assert self.RED in line
        assert exit_code == 0

    def test_a_schema_at_head_is_green(self, monkeypatch: Any) -> None:
        """The one state that needs nothing."""
        _use_fake_session(monkeypatch)

        line, _ = self._colour_of_schema_line(monkeypatch)

        assert self.GREEN in line
        assert self.RED not in line

    def test_a_fresh_database_is_yellow(self, monkeypatch: Any) -> None:
        """Expected on the way to a working database, so not an alarm."""
        _use_fake_session(monkeypatch, None, tables=False)

        line, _ = self._colour_of_schema_line(monkeypatch)

        assert self.YELLOW in line


def test_a_database_url_that_cannot_be_used_is_named_not_traced(unusable_dsn: str) -> None:
    """A blank `FANTABOT_DATABASE_URL` fails when the engine is built, which is inside the
    handler's `try` — and a handler that rendered the DSN with `safe_dsn` would raise
    `InvalidDsn` again from inside the `except`.

    The real `database_manager` is used (see the fixture): what the screen does with the
    exception it really gets is the thing under test.
    """
    result = runner.invoke(app, ["db", "check"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), "an exit code, not a traceback"
    assert "FANTABOT_DATABASE_URL" in result.output
    assert ("blank" if not unusable_dsn.strip() else "does not parse") in result.output
    assert "fantabot-app db start" not in result.output, "the server is not what is wrong"
    assert "ArgumentError (" not in result.output, "no connection was made to classify"


def test_a_dsn_that_looks_like_rich_markup_is_printed_not_interpreted(monkeypatch: Any) -> None:
    """The DSN is configuration text. A `[/red]` in a database name is a `MarkupError` unless
    the headline is escaped, and that error would replace the report of an unreachable
    database with a traceback."""
    from fantabot import config
    from fantabot.adapters.persistence import database_manager

    class _Dead:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def commit(self) -> None: ...

        def rollback(self) -> None: ...

        def close(self) -> None: ...

    monkeypatch.setattr(database_manager, "_session_factory", _Dead)
    monkeypatch.setattr(
        config.settings, "fantabot_database_url", "postgresql+psycopg2://u@h:5432/[/red]db"
    )

    result = runner.invoke(app, ["db", "check"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "[/red]db" in result.output
