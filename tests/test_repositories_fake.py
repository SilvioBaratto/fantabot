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


class TestLeagueTokenRepository:
    """The token store's SQL, verified without a database.

    The upsert is the dangerous one. Its `SET` clause has to name every mutable
    column, and the failure mode of missing one is silent and inverted: a row
    carrying a new ciphertext beside an old fingerprint makes `decrypt` tell the
    operator to restore a key that is not the problem.
    """

    @staticmethod
    def _row() -> Any:
        from datetime import UTC, datetime

        from fantabot.db.models.tokens import LeagueToken

        now = datetime(2026, 8, 26, tzinfo=UTC)
        return LeagueToken(
            league_id=4103937,
            ciphertext=b"gAAAAA-not-a-real-fernet-token",
            key_fingerprint="4f2a1c8e",
            issued_at=now,
            expires_at=datetime(2027, 8, 19, tzinfo=UTC),
            user_id=20000003,
            team_id=10000003,
            league_name="Legamiallerotaie2",
            captured_at=now,
            last_seen_at=now,
            last_verified_at=None,
        )

    def test_upsert_issues_exactly_one_statement(self) -> None:
        session = _session()
        from fantabot.db.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).upsert(self._row())

        assert len(session.statements) == 1
        assert "ON CONFLICT (league_id) DO UPDATE" in session.statements[0]

    def test_the_set_clause_names_every_mutable_column(self) -> None:
        """Derived from the model, so a column added later fails here.

        A hand-written list is exactly how `key_fingerprint` gets dropped.
        """
        from fantabot.db.models.tokens import LeagueToken
        from fantabot.db.repositories.tokens import UPSERT_COLUMNS, LeagueTokenRepository

        expected = {
            c.name for c in LeagueToken.__table__.columns
        } - {"league_id", "created_at"}
        assert set(UPSERT_COLUMNS) == expected

        session = _session()
        LeagueTokenRepository(session).upsert(self._row())
        set_clause = session.statements[0].split("DO UPDATE SET", 1)[1]

        for column in expected:
            assert f"{column} =" in set_clause, f"{column} is not overwritten by the upsert"

    def test_the_fingerprint_is_overwritten_alongside_the_ciphertext(self) -> None:
        """Named explicitly because this is the trap the derived list prevents."""
        session = _session()
        from fantabot.db.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).upsert(self._row())
        set_clause = session.statements[0].split("DO UPDATE SET", 1)[1]

        assert "ciphertext =" in set_clause
        assert "key_fingerprint =" in set_clause

    def test_last_verified_at_is_reset_by_an_upsert(self) -> None:
        """A new credential is not verified because its predecessor was."""
        session = _session()
        from fantabot.db.repositories.tokens import LeagueTokenRepository

        row = self._row()
        from datetime import UTC, datetime

        row.last_verified_at = datetime(2026, 8, 26, tzinfo=UTC)
        LeagueTokenRepository(session).upsert(row)

        assert "last_verified_at =" in session.statements[0].split("DO UPDATE SET", 1)[1]

    def test_touch_last_seen_with_an_empty_list_issues_no_statement(self) -> None:
        session = _session()
        from datetime import UTC, datetime

        from fantabot.db.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).touch_last_seen([], datetime.now(UTC))

        assert session.statements == []

    def test_touch_last_seen_batches_into_one_statement(self) -> None:
        """`login --league X` stamps every lega it saw while rewriting only X."""
        session = _session()
        from datetime import UTC, datetime

        from fantabot.db.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).touch_last_seen([3584692, 4103937], datetime.now(UTC))

        assert len(session.statements) == 1
        assert "UPDATE league_tokens" in session.statements[0]

    def test_all_rows_orders_explicitly(self) -> None:
        """Postgres has no inherent row order; unordered output would shuffle."""
        session = _session([])
        from fantabot.db.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).all_rows()

        assert "ORDER BY league_tokens.league_id" in session.statements[0]

    def test_all_rows_selects_no_ciphertext(self) -> None:
        """Nothing that renders a status needs one, so nothing gets one."""
        session = _session([])
        from fantabot.db.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).all_rows()

        assert "ciphertext" not in session.statements[0]

    def test_the_repository_never_imports_the_cipher(self) -> None:
        """Decryption is the store's job, and the store is the only site."""
        from pathlib import Path

        source = Path("src/fantabot/db/repositories/tokens.py").read_text()

        assert "tokens.crypto" not in source
        assert "decrypt(" not in source
