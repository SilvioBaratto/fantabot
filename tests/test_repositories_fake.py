"""Repository behaviour, verified against a fake session. No database.

Repositories take a ``Session`` and never build one, so the narrow protocol they
actually use — ``execute`` returning something with ``scalar`` and ``fetchone``
— is cheap to fake. That is what keeps this tier socket-free while still pinning
real behaviour: which statements are issued, in what order, and what is refused
before any SQL is built at all.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import BigInteger, Column, MetaData, Table

import fantabot.db.models  # noqa: F401  -- registers every table on Base.metadata
from fantabot.db.base import Base
from fantabot.db.repositories.admin import AdminRepository, UnknownTableError


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value

    def fetchone(self) -> Any:
        return (self._value,)


class _FakeSession:
    """Records every statement, and answers with a queue of canned values."""

    def __init__(self, answers: list[Any] | None = None) -> None:
        self.statements: list[str] = []
        self._answers = list(answers or [])

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        self.statements.append(str(statement))
        return _FakeResult(self._answers.pop(0) if self._answers else 1)


def _session(*answers: Any) -> Any:
    return _FakeSession(list(answers))


class TestTruncateIsAllowlisted:
    def test_an_injection_attempt_raises_before_any_sql_is_built(self) -> None:
        session = _session()
        repo = AdminRepository(session)

        with pytest.raises(UnknownTableError):
            repo.truncate("players; drop table voti")

        assert session.statements == [], "a statement was built for an unknown table"

    def test_a_name_absent_from_the_metadata_is_rejected(self) -> None:
        session = _session()
        with pytest.raises(UnknownTableError, match="not a table"):
            AdminRepository(session).truncate("definitely_not_a_table")

        assert session.statements == []

    def test_a_known_table_is_truncated_with_cascade(self) -> None:
        session = _session()
        known = next(iter(Base.metadata.tables))

        AdminRepository(session).truncate(known)

        assert session.statements == [f'TRUNCATE TABLE "{known}" CASCADE']


class TestTableListIsDerivedNotHardcoded:
    def test_a_table_added_to_the_metadata_appears_with_no_code_change(self) -> None:
        """The point of deriving it: a migration in a later phase must not
        require editing this repository to show up in db-check."""
        metadata = MetaData()
        Table("later_phase_table", metadata, Column("id", BigInteger, primary_key=True))

        repo = AdminRepository(_session(), metadata=metadata)

        assert repo.table_names == ["later_phase_table"]

    def test_the_default_metadata_is_the_declarative_base(self) -> None:
        repo = AdminRepository(_session())
        assert set(repo.table_names) == set(Base.metadata.tables)

    def test_stats_are_reported_for_every_declared_table(self) -> None:
        metadata = MetaData()
        Table("one", metadata, Column("id", BigInteger, primary_key=True))
        Table("two", metadata, Column("id", BigInteger, primary_key=True))

        stats = AdminRepository(_session(), metadata=metadata).table_stats()

        assert [row["name"] for row in stats] == ["one", "two"]

    def test_a_missing_table_is_reported_rather_than_raising(self) -> None:
        """db-check runs before the first migration too, and should say so."""
        metadata = MetaData()
        Table("absent", metadata, Column("id", BigInteger, primary_key=True))

        # First answer: information_schema says the table does not exist.
        stats = AdminRepository(_session(False), metadata=metadata).table_stats()

        assert stats == [
            {
                "name": "absent",
                "exists": False,
                "row_count": None,
                "size_bytes": None,
                "size_pretty": "—",
            }
        ]


class TestHealth:
    def test_a_working_session_reports_ok_with_a_latency(self) -> None:
        ok, latency_ms = AdminRepository(_session(1)).health()

        assert ok is True
        assert latency_ms >= 0

    def test_a_broken_session_reports_not_ok_rather_than_raising(self) -> None:
        class _Broken:
            def execute(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("connection refused")

        ok, latency_ms = AdminRepository(_Broken()).health()

        assert ok is False
        assert latency_ms >= 0
