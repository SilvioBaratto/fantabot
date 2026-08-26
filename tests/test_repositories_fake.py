"""Repository behaviour, verified against a fake session. No database.

Repositories take a ``Session`` and never build one, so the narrow protocol they
actually use — ``execute`` returning something with ``scalar`` and ``fetchone``
— is cheap to fake. That is what keeps this tier socket-free while still pinning
real behaviour: which statements are issued, in what order, and what is refused
before any SQL is built at all.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import BigInteger, Column, MetaData, Table
from sqlalchemy.dialects import postgresql

import fantabot.db.models  # noqa: F401  -- registers every table on Base.metadata
from fantabot.db.base import Base
from fantabot.db.repositories.admin import AdminRepository, UnknownTableError
from fantabot.db.repositories.sentiment import (
    SentimentReadRepository,
    SentimentRepository,
    to_record,
)


def _sentiment_row(**overrides: str) -> dict[str, str]:
    """One store.build_row output, all cells stringly typed as it emits them."""
    row = {
        "data_run": "2026-10-07",
        "giorni_lookback": "14",
        "stagione": "2026/27",
        "id": "6916",
        "nome": "Ahanor",
        "squadra": "ATA",
        "ruolo": "Difensore",
        "ruoli_mantra": "B;DS;E",
        "ruolo_campo": "B;DS",
        "deriva_ruolo": "0.70",
        "sentiment": "-0.40",
        "disponibilita": "0.20",
        "titolarita": "0.30",
        "mercato": "-0.60",
        "forma": "0.00",
        "rigorista": "0.00",
        "piazzati": "0.00",
        "confidenza": "0.70",
        "riassunto": "Infortunio muscolare.",
        "n_fonti": "2",
        "fonti": "https://a;https://b",
        "modello": "test",
    }
    row.update(overrides)
    return row


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value

    def fetchone(self) -> Any:
        return (self._value,)

    def all(self) -> Any:
        return self._value if isinstance(self._value, list) else []

    def scalars(self) -> Any:
        return self

    def scalar_one_or_none(self) -> Any:
        return None


class _FakeSession:
    """Records every statement, and answers with a queue of canned values."""

    def __init__(self, answers: list[Any] | None = None) -> None:
        self.statements: list[str] = []
        self._answers = list(answers or [])

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        # Compiled against the Postgres dialect, not str(): DISTINCT ON and
        # ON CONFLICT are dialect-specific and render as nothing generically,
        # so a generic string would make every SQL assertion here vacuous.
        try:
            rendered = str(statement.compile(dialect=postgresql.dialect()))
        except Exception:  # pragma: no cover - defensive
            rendered = str(statement)
        self.statements.append(rendered)
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


class TestSentimentWritePath:
    """The empty-batch no-op and the string key shape, without a database."""

    def test_an_empty_batch_issues_no_statement(self) -> None:
        """store.append_rows returns before touching the filesystem, so a run
        that produced nothing leaves no artefact. Same contract here."""
        session = _session()

        sent = SentimentRepository(session).upsert_rows([])

        assert sent == 0
        assert session.statements == []

    def test_a_non_empty_batch_issues_exactly_one_statement(self) -> None:
        session = _session()

        sent = SentimentRepository(session).upsert_rows([_sentiment_row()])

        assert sent == 1
        assert len(session.statements) == 1

    def test_force_produces_an_update_and_the_default_a_do_nothing(self) -> None:
        plain = _session()
        SentimentRepository(plain).upsert_rows([_sentiment_row()])

        forced = _session()
        SentimentRepository(forced).upsert_rows([_sentiment_row()], force=True)

        assert "DO NOTHING" in plain.statements[0].upper()
        assert "DO UPDATE" in forced.statements[0].upper()


class TestSentimentRecordConversion:
    """build_row emits strings because its target was a CSV. The typing lives
    here so build_row stays pure and its eleven tests keep describing it."""

    def test_the_run_date_becomes_a_date(self) -> None:
        assert to_record(_sentiment_row())["data_run"] == date(2026, 10, 7)

    def test_the_player_id_becomes_an_integer_for_the_foreign_key(self) -> None:
        assert to_record(_sentiment_row())["player_id"] == 6916

    def test_scores_keep_two_decimal_places_as_decimals(self) -> None:
        record = to_record(_sentiment_row())

        assert record["confidenza"] == Decimal("0.70")
        assert record["deriva_ruolo"] == Decimal("0.70")

    def test_sources_are_split_into_an_array(self) -> None:
        assert to_record(_sentiment_row())["fonti"] == ["https://a", "https://b"]

    def test_no_sources_is_an_empty_array_not_a_list_containing_empty(self) -> None:
        record = to_record(_sentiment_row(fonti="", n_fonti="0"))

        assert record["fonti"] == []


class TestDriftedIsOneStatement:
    """Not one query per player. With 523 players the difference is 523
    round-trips against 1, and the CSV version's whole-file slurp is what this
    replaces."""

    def test_it_issues_exactly_one_statement(self) -> None:
        session = _session([])

        SentimentReadRepository(session).drifted()

        assert len(session.statements) == 1

    def test_the_statement_takes_the_latest_row_per_player(self) -> None:
        session = _session([])

        SentimentReadRepository(session).drifted()

        sql = session.statements[0].upper()
        assert "DISTINCT ON" in sql
        assert "ORDER BY" in sql
