"""Wiring the two sides of the schema comparison, and what happens when one cannot be read.

The verdicts themselves are pinned in `test_schema_revision.py`; this file is about the seam
between the database, the scripts and the three surfaces that ask. The property that matters
most is negative: **a failure to determine the revision is a verdict, not an exception.** The
hourly lineup job calls this before it submits, and a diagnostic that could stop a submit
would cost more than the drift it reports.

Everything runs against fakes. No database, no alembic project of its own: the scripts side
is a canned `ScriptRevisions`, and the database side is a session whose answers are queued.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from fantabot.application import schema_check
from fantabot.application.schema_check import WAIT_SECONDS, check_database_schema, check_schema
from fantabot.domain.shared.schema_revision import SchemaState, ScriptRevisions

HEAD = "b61ab22e8fac"
PREVIOUS = "e35dbc3294a7"
SCRIPTS = ScriptRevisions(heads=(HEAD,), pending={HEAD: 0, PREVIOUS: 1})


def _scripts() -> ScriptRevisions:
    return SCRIPTS


def _revisions(*rows: str):  # type: ignore[no-untyped-def]
    return lambda: tuple(rows)


class TestCheckSchema:
    """The comparison, with both reads injected."""

    def test_a_database_on_the_head_is_at_head(self) -> None:
        """The happy path, end to end through the seam."""
        verdict = check_schema(_revisions(HEAD), read_scripts=_scripts)

        assert verdict.state is SchemaState.AT_HEAD

    def test_a_database_behind_the_head_is_behind(self) -> None:
        """The measured failure, with the distance carried through."""
        verdict = check_schema(_revisions(PREVIOUS), read_scripts=_scripts)

        assert verdict.state is SchemaState.BEHIND
        assert verdict.behind == 1

    def test_a_missing_alembic_version_table_is_unversioned(self) -> None:
        """The repository returns `None` for an absent table; the seam passes it on."""
        verdict = check_schema(lambda: None, read_scripts=_scripts)

        assert verdict.state is SchemaState.UNVERSIONED

    def test_no_scripts_is_scripts_unavailable(self) -> None:
        """A wheel install: the database is read, the head cannot be."""
        verdict = check_schema(_revisions(HEAD), read_scripts=lambda: None)

        assert verdict.state is SchemaState.SCRIPTS_UNAVAILABLE

    def test_a_database_that_cannot_be_asked_is_unreadable_not_raised(self) -> None:
        """The hourly job must never stop on this."""

        def _down() -> tuple[str, ...]:
            raise OperationalError("SELECT", {}, Exception("connection refused"))

        verdict = check_schema(_down, read_scripts=_scripts)

        assert verdict.state is SchemaState.UNREADABLE
        assert verdict.error == "OperationalError"

    def test_the_driver_message_never_reaches_the_verdict(self) -> None:
        """It carries the DSN; the verdict is printed into logs that get pasted."""

        def _down() -> tuple[str, ...]:
            raise OperationalError(
                "SELECT", {}, Exception("password=SchemaCanary host=localhost")
            )

        verdict = check_schema(_down, read_scripts=_scripts)

        assert "SchemaCanary" not in repr(verdict)
        assert "SchemaCanary" not in verdict.summary

    def test_a_wiring_bug_is_not_reported_as_an_unhealthy_database(self) -> None:
        """Only `SQLAlchemyError` is absorbed. An `AttributeError` means a fake or an adapter
        is wired wrong, and calling that "the database could not be read" is how a bug turns
        into a week of looking at Postgres."""

        def _broken() -> tuple[str, ...]:
            raise AttributeError("no attribute 'execute'")

        with pytest.raises(AttributeError):
            check_schema(_broken, read_scripts=_scripts)

    def test_the_scripts_are_not_read_when_the_database_could_not_be(self) -> None:
        """No point loading every migration module to describe a connection failure."""
        loaded: list[bool] = []

        def _down() -> tuple[str, ...]:
            raise OperationalError("SELECT", {}, Exception("refused"))

        check_schema(_down, read_scripts=lambda: loaded.append(True))  # type: ignore[arg-type, return-value]

        assert loaded == []

    def test_the_default_scripts_reader_is_looked_up_when_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A default bound at definition would make the adapter impossible to substitute."""
        monkeypatch.setattr(schema_check, "load_script_revisions", _scripts)

        assert check_schema(_revisions(HEAD)).state is SchemaState.AT_HEAD


    @pytest.mark.parametrize(
        "raised",
        [
            ImportError("boom"),
            ModuleNotFoundError("alembic.config"),
            SyntaxError("half-written migration"),
            RuntimeError("anything a migration module does at import"),
        ],
    )
    def test_a_migration_module_that_will_not_load_is_a_verdict_not_a_traceback(
        self, raised: Exception
    ) -> None:
        """Loading the scripts *executes* every migration file, and a half-written one in a
        checkout somebody is editing raises whatever it likes. That must not take the screen
        or the hourly job's preamble down with it."""

        def _broken() -> ScriptRevisions:
            raise raised

        verdict = check_schema(_revisions(HEAD), read_scripts=_broken)

        assert verdict.state is SchemaState.UNREADABLE
        assert verdict.error == type(raised).__name__
        assert "could not be checked" in verdict.summary

    def test_what_a_broken_migration_says_never_reaches_the_verdict(self) -> None:
        """Only the type name is carried, as for a driver error."""

        def _broken() -> ScriptRevisions:
            raise ImportError("cannot import name 'SchemaCanary' from 'secret.module'")

        verdict = check_schema(_revisions(HEAD), read_scripts=_broken)

        assert "SchemaCanary" not in repr(verdict)

    def test_a_missing_revision_table_on_a_database_with_tables_is_unversioned(self) -> None:
        """Tables and no `alembic_version`: the case that must not be told to upgrade."""
        verdict = check_schema(lambda: None, read_scripts=_scripts, read_empty=lambda: False)

        assert verdict.state is SchemaState.UNVERSIONED

    def test_no_revision_on_a_database_with_no_tables_is_empty(self) -> None:
        """A fresh `fantabot-app db create`."""
        verdict = check_schema(lambda: None, read_scripts=_scripts, read_empty=lambda: True)

        assert verdict.state is SchemaState.EMPTY
        assert verdict.heads == (HEAD,)

    def test_emptiness_is_not_asked_when_there_is_a_revision(self) -> None:
        """A database that holds one is judged on it; the extra probe would be one query per
        table for an answer nobody reads."""
        asked: list[bool] = []

        def _empty() -> bool:
            asked.append(True)
            return True

        verdict = check_schema(_revisions(HEAD), read_scripts=_scripts, read_empty=_empty)

        assert verdict.state is SchemaState.AT_HEAD
        assert asked == []

    def test_emptiness_failing_on_the_database_is_a_verdict(self) -> None:
        """It is a read like the other, so it fails like the other."""

        def _down() -> bool:
            raise OperationalError("SELECT", {}, Exception("connection refused"))

        verdict = check_schema(lambda: None, read_scripts=_scripts, read_empty=_down)

        assert verdict.state is SchemaState.UNREADABLE

    def test_a_table_with_no_row_and_no_tables_is_empty_too(self) -> None:
        """`alembic downgrade base`: the version table survives, holding nothing."""
        verdict = check_schema(_revisions(), read_scripts=_scripts, read_empty=lambda: True)

        assert verdict.state is SchemaState.EMPTY


class _Result:
    """The slice of a SQLAlchemy result the repository touches."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        """The canned value, as `Result.scalar` returns the first column of the first row."""
        return self._value

    def scalars(self) -> _Result:
        """Itself: the canned value is already a flat column."""
        return self

    def all(self) -> Any:
        """The canned rows, or none when the answer was not a list."""
        return self._value if isinstance(self._value, list) else []


class _Session:
    """Answers what the repository asks: which tables exist, then the rows of one.

    `rows` is what `alembic_version` holds, or `None` when that table is absent; `tables`
    is whether the application's own tables are there.
    """

    def __init__(self, rows: list[str] | None, tables: bool = True) -> None:
        self._rows = rows
        self._tables = tables
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        """The existence probe is answered per table name; everything else is recorded."""
        self.statements.append((str(statement), dict(params or {})))
        if "information_schema" in str(statement):
            if (params or {}).get("name") == "alembic_version":
                return _Result(self._rows is not None)
            return _Result(self._tables)
        return _Result(self._rows)

    def commit(self) -> None:
        """Part of the session protocol `get_session` drives."""
        self.commits += 1

    def rollback(self) -> None:
        """Part of the session protocol `get_session` drives."""

    def close(self) -> None:
        """Part of the session protocol `get_session` drives."""


class _DeadSession(_Session):
    """A server that is not running: the connection fails on first use, as it does for real."""

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        """Always refuses, because the connection is opened lazily at the first statement."""
        raise OperationalError("SELECT", {}, Exception("connection refused"))


class _FailsToCommit(_Session):
    """Reads fine and then fails when the transaction ends, which `check_schema` cannot see."""

    def commit(self) -> None:
        """The session's own failure, after every read has succeeded."""
        raise OperationalError("COMMIT", {}, Exception("server closed the connection"))


def _use(
    monkeypatch: pytest.MonkeyPatch,
    session: type[_Session],
    rows: list[str] | None,
    *,
    tables: bool = True,
) -> list[_Session]:
    """Route `database_manager` to `session`; returns every session it hands out."""
    from fantabot.adapters.persistence import database_manager

    handed: list[_Session] = []

    def _factory() -> _Session:
        handed.append(session(rows, tables))
        return handed[-1]

    monkeypatch.setattr(database_manager, "_session_factory", _factory)
    monkeypatch.setattr(schema_check, "load_script_revisions", _scripts)
    return handed


class TestCheckDatabaseSchema:
    """The one-call form the launcher uses: it opens its own session."""

    def test_it_reads_the_revision_through_a_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rows in, verdict out, with no session handed in."""
        _use(monkeypatch, _Session, [PREVIOUS])

        assert check_database_schema().state is SchemaState.BEHIND

    def test_a_database_with_tables_and_no_version_table_is_unversioned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tables created by something other than alembic: the dangerous case."""
        _use(monkeypatch, _Session, None, tables=True)

        assert check_database_schema().state is SchemaState.UNVERSIONED

    def test_a_database_with_no_tables_at_all_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`fantabot_test` before its first `alembic upgrade head`, or a fresh `db create`."""
        _use(monkeypatch, _Session, None, tables=False)

        verdict = check_database_schema()

        assert verdict.state is SchemaState.EMPTY
        assert verdict.instruction is not None
        assert "dump" not in verdict.instruction

    def test_an_unreachable_database_is_a_verdict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A stopped server must not raise out of `schedule status`. The first statement on
        the connection fails before `check_schema` is reached, so the guard that turns it
        into a verdict is this function's own."""
        _use(monkeypatch, _DeadSession, None)

        verdict = check_database_schema()

        assert verdict.state is SchemaState.UNREADABLE
        assert verdict.error == "OperationalError"

    def test_a_failure_to_end_the_transaction_is_a_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The commit happens after `check_schema` has returned; its failure is not one it saw."""
        _use(monkeypatch, _FailsToCommit, [HEAD])

        verdict = check_database_schema()

        assert verdict.state is SchemaState.UNREADABLE
        assert verdict.error == "OperationalError"

    def test_an_engine_that_cannot_be_built_is_a_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed DSN is an `ArgumentError`, raised before any session exists."""
        from sqlalchemy.exc import ArgumentError

        from fantabot.adapters.persistence import database_manager

        def _refuse() -> Any:
            raise ArgumentError("Could not parse SQLAlchemy URL")

        monkeypatch.setattr(database_manager, "_session_factory", _refuse)

        verdict = check_database_schema()

        assert verdict.state is SchemaState.UNREADABLE
        assert verdict.error == "ArgumentError"

    def test_the_waits_are_bounded_before_anything_is_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hung postmaster or a lock on `alembic_version` must not stall the launcher
        ahead of the submit, which has walls of its own. The bound is set first, and in the
        session's own transaction so it ends with it."""
        sessions = _use(monkeypatch, _Session, [HEAD])

        check_database_schema()

        statements = sessions[0].statements
        first_two = statements[:2]
        assert all("set_config" in text for text, _ in first_two)
        assert [params["setting"] for _, params in first_two] == [
            "statement_timeout",
            "lock_timeout",
        ]
        assert {params["value"] for _, params in first_two} == {str(WAIT_SECONDS * 1000)}
        assert all(text.rstrip().endswith("true)") for text, _ in first_two)
        assert all("alembic_version" not in text for text, _ in first_two)

    def test_the_session_is_committed_once_it_has_been_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`SET LOCAL` ends with the transaction; a pooled connection must not keep it."""
        sessions = _use(monkeypatch, _Session, [HEAD])

        check_database_schema()

        assert sessions[0].commits == 1
