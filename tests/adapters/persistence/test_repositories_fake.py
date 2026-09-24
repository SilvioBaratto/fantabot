"""Repository behaviour, verified against a fake session. No database.

Repositories take a ``Session`` and never build one, so the narrow protocol they
actually use — ``execute`` returning something with ``scalar`` and ``fetchone``
— is cheap to fake. That is what keeps this tier socket-free while still pinning
real behaviour: which statements are issued, in what order, and what is refused
before any SQL is built at all.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import BigInteger, Column, MetaData, Table
from sqlalchemy.dialects import postgresql

import fantabot.adapters.persistence.models  # noqa: F401  -- registers every table on Base.metadata
from fantabot.adapters.persistence.base import Base
from fantabot.adapters.persistence.repositories.admin import AdminRepository, UnknownTableError
from fantabot.adapters.persistence.repositories.lineup_history import (
    BacktestCorpusRepository,
    ShadowRepository,
)
from fantabot.adapters.persistence.repositories.sentiment import (
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

    def __init__(self, answers: list[Any] | None = None, *, literal: bool = False) -> None:
        self.statements: list[str] = []
        self._answers = list(answers or [])
        # Bound parameters render as `:asta_type_1` by default, which is right for the
        # structural assertions here and useless for asserting *which value* a filter
        # carries. Opt in per session rather than always: literal binds fail to render
        # for some types, and every existing assertion is about shape, not values.
        self._literal = literal

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        # Compiled against the Postgres dialect, not str(): DISTINCT ON and
        # ON CONFLICT are dialect-specific and render as nothing generically,
        # so a generic string would make every SQL assertion here vacuous.
        kwargs = {"compile_kwargs": {"literal_binds": True}} if self._literal else {}
        try:
            rendered = str(statement.compile(dialect=postgresql.dialect(), **kwargs))
        except Exception:  # pragma: no cover - defensive
            rendered = str(statement)
        self.statements.append(rendered)
        return _FakeResult(self._answers.pop(0) if self._answers else 1)


def _session(*answers: Any, literal: bool = False) -> Any:
    return _FakeSession(list(answers), literal=literal)


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
        require editing this repository to show up in db check."""
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
        """db check runs before the first migration too, and should say so."""
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

        from fantabot.adapters.persistence.models.tokens import LeagueToken

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
        from fantabot.adapters.persistence.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).upsert(self._row())

        assert len(session.statements) == 1
        assert "ON CONFLICT (league_id) DO UPDATE" in session.statements[0]

    def test_the_set_clause_names_every_mutable_column(self) -> None:
        """Derived from the model, so a column added later fails here.

        A hand-written list is exactly how `key_fingerprint` gets dropped.
        """
        from fantabot.adapters.persistence.models.tokens import LeagueToken
        from fantabot.adapters.persistence.repositories.tokens import (
            UPSERT_COLUMNS,
            LeagueTokenRepository,
        )

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
        from fantabot.adapters.persistence.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).upsert(self._row())
        set_clause = session.statements[0].split("DO UPDATE SET", 1)[1]

        assert "ciphertext =" in set_clause
        assert "key_fingerprint =" in set_clause

    def test_last_verified_at_is_reset_by_an_upsert(self) -> None:
        """A new credential is not verified because its predecessor was."""
        session = _session()
        from fantabot.adapters.persistence.repositories.tokens import LeagueTokenRepository

        row = self._row()
        from datetime import UTC, datetime

        row.last_verified_at = datetime(2026, 8, 26, tzinfo=UTC)
        LeagueTokenRepository(session).upsert(row)

        assert "last_verified_at =" in session.statements[0].split("DO UPDATE SET", 1)[1]

    def test_touch_last_seen_with_an_empty_list_issues_no_statement(self) -> None:
        session = _session()
        from datetime import UTC, datetime

        from fantabot.adapters.persistence.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).touch_last_seen([], datetime.now(UTC))

        assert session.statements == []

    def test_touch_last_seen_batches_into_one_statement(self) -> None:
        """`login --league X` stamps every lega it saw while rewriting only X."""
        session = _session()
        from datetime import UTC, datetime

        from fantabot.adapters.persistence.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).touch_last_seen([3584692, 4103937], datetime.now(UTC))

        assert len(session.statements) == 1
        assert "UPDATE league_tokens" in session.statements[0]

    def test_all_rows_orders_explicitly(self) -> None:
        """Postgres has no inherent row order; unordered output would shuffle."""
        session = _session([])
        from fantabot.adapters.persistence.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).all_rows()

        assert "ORDER BY league_tokens.league_id" in session.statements[0]

    def test_all_rows_selects_no_ciphertext(self) -> None:
        """Nothing that renders a status needs one, so nothing gets one."""
        session = _session([])
        from fantabot.adapters.persistence.repositories.tokens import LeagueTokenRepository

        LeagueTokenRepository(session).all_rows()

        assert "ciphertext" not in session.statements[0]

    def test_the_repository_never_imports_the_cipher(self) -> None:
        """Decryption is the store's job, and the store is the only site."""
        from _paths import pkg

        source = (pkg("db") / "repositories" / "tokens.py").read_text()

        assert "tokens.crypto" not in source
        assert "decrypt(" not in source


class TestClearingSalesFilterOnTheFormatTheyAreGiven:
    """The format is a parameter, because there are now two corpora and only one was read.

    `mantra_clearing_sales` named the format in its own identifier and pinned
    `asta_type = 'mantra'` in the filter, so `read_plan_inputs` had to guard the call with
    `if listone == "mantra" ... else []`. Every Classic run therefore priced from `fvm`
    alone — silently, because an empty corpus is a legal input to `mean_prices`.

    Measured 2026-09-05, after the Classic landing zone was re-loaded: 32,100 Classic sales
    over 453 players in 259 rooms of our own 8x500 shape, against 6,625 Mantra sales over
    424 players in 49 rooms. The larger corpus was the unread one.
    """

    def test_the_format_reaches_the_where_clause(self) -> None:
        from fantabot.adapters.persistence.repositories.aste import AsteRepository
        from fantabot.domain.asta.prices import NoCorpus

        for asta_type in ("classic", "mantra"):
            session = _session([], literal=True)
            # Since 1.6 an empty result is a refusal, not an empty list. The query is
            # still built and is still what this asserts on.
            with pytest.raises(NoCorpus):
                AsteRepository(session).clearing_sales(asta_type=asta_type)

            where = session.statements[0].split("WHERE", 1)[1]
            assert f"asta.asta_type = '{asta_type}'" in where

    def test_mantra_stays_the_default_so_no_caller_has_to_change(self) -> None:
        from fantabot.adapters.persistence.repositories.aste import AsteRepository
        from fantabot.domain.asta.prices import NoCorpus

        session = _session([], literal=True)
        with pytest.raises(NoCorpus):
            AsteRepository(session).clearing_sales()

        assert "asta.asta_type = 'mantra'" in session.statements[0]

    def test_an_unknown_format_raises_before_any_sql_is_built(self) -> None:
        """A typo must not read as "no sales": that is the failure this whole class is about.

        `asta_type` is free text in the column, so an unrecognised value would return an
        empty list and price a whole roster from `fvm` — the exact silence being removed.
        """
        from fantabot.adapters.persistence.repositories.aste import AsteRepository

        session = _session([])
        with pytest.raises(ValueError, match="asta_type"):
            AsteRepository(session).clearing_sales(asta_type="Mantra")

        assert session.statements == []


class TestClearingSalesAreReadInAStableOrder:
    """`clearing_sales` feeds the golden fixture, so its row order is load-bearing.

    Postgres has no inherent order, and this query had no `ORDER BY`. Today that is
    harmless — `prices.mean_prices` sums ints, and integer addition is associative — but
    the fixture the golden harness pins is captured from exactly these rows. Without a
    total order, re-capturing it produces a diff that is indistinguishable from real
    drift, and a gate that cries wolf gets regenerated instead of investigated.

    `(fantacalcio_id, price)` is a total order over the projected columns, which
    `fantacalcio_id` alone is not: a player sold in several auctions has several rows.
    """

    def test_the_query_orders_by_player_then_price(self) -> None:
        session = _session([])
        from fantabot.adapters.persistence.repositories.aste import AsteRepository
        from fantabot.domain.asta.prices import NoCorpus

        with pytest.raises(NoCorpus):  # the fake returns no rows; 1.6 refuses that
            AsteRepository(session).clearing_sales()

        sql = session.statements[0]
        assert "ORDER BY" in sql, "clearing sales are read in whatever order Postgres returns"
        order_by = sql.split("ORDER BY", 1)[1]
        assert "asta_assignment.fantacalcio_id" in order_by
        assert "asta_assignment.price" in order_by


class TestTheFantalabSessionRepositoryHandlesBytesOnly:
    """The SQL half of `FantalabStore`, extracted so it stops being the one
    credential path that reached SQLAlchemy directly.

    The division is the same one `LeagueTokenRepository` keeps and
    `tests/adapters/tokens/test_token_secrecy.py` enforces: the repository moves ciphertext as
    bytes and never names a cipher; the store decrypts. Both halves are asserted
    here because a repository that quietly grew a `decrypt` would still pass every
    behavioural test in the suite.
    """

    def test_describe_selects_no_ciphertext(self) -> None:
        """A status command must be writable without a decrypt."""
        session = _session([])
        from fantabot.adapters.persistence.repositories.tokens import FantalabSessionRepository

        FantalabSessionRepository(session).describe()

        sql = session.statements[0]
        assert "ciphertext" not in sql
        assert "key_fingerprint" not in sql

    def test_key_fingerprints_selects_the_fingerprint_and_no_ciphertext(self) -> None:
        """The Accounts page's mismatch check needs the stamp, never the secret."""
        session = _session([])
        from fantabot.adapters.persistence.repositories.tokens import FantalabSessionRepository

        FantalabSessionRepository(session).key_fingerprints()

        sql = session.statements[0]
        assert "key_fingerprint" in sql
        assert "ciphertext" not in sql

    def test_describe_orders_newest_first(self) -> None:
        """`load()` with no user_id promises the most recent row wins."""
        session = _session([])
        from fantabot.adapters.persistence.repositories.tokens import FantalabSessionRepository

        FantalabSessionRepository(session).describe()

        assert "ORDER BY fantalab_session.captured_at DESC" in session.statements[0]

    def test_upsert_clears_last_used_at(self) -> None:
        """A re-captured session has not been used yet.

        Carrying the old stamp forward would claim otherwise, which is the kind of
        thing a status table states confidently and wrongly.
        """
        session = _session([])
        from fantabot.adapters.persistence.repositories.tokens import FantalabSessionRepository

        FantalabSessionRepository(session).upsert(
            user_id="u1", ciphertext=b"x", fingerprint="fp", at=datetime(2026, 8, 30)
        )

        set_clause = session.statements[0].split("DO UPDATE SET", 1)[1]
        assert "last_used_at" in set_clause

    def test_the_repository_never_imports_the_cipher(self) -> None:
        """Decryption is the store's job, and the store is the only site."""
        from _paths import pkg

        source = (pkg("db") / "repositories" / "tokens.py").read_text()

        assert "tokens.crypto" not in source
        assert "decrypt(" not in source


# --- LeagueRepository.purge -----------------------------------------------------------


class _RecordingSession:
    """Records every statement compiled to SQL, and answers the id lookup."""

    def __init__(self, competition_ids: list[int] | None = None) -> None:
        self.statements: list[str] = []
        self._competition_ids = competition_ids if competition_ids is not None else []

    def execute(self, statement: Any) -> Any:
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.statements.append(sql)
        session = self

        class _Result:
            rowcount = 1

            def scalars(self) -> Any:
                class _Scalars:
                    @staticmethod
                    def all() -> list[int]:
                        return session._competition_ids

                return _Scalars()

        return _Result()


def test_purge_resolves_competition_ids_before_deleting_the_competitions() -> None:
    """The one ordering constraint, and the only one that cannot be recovered from.

    `league_fixture` has no `league_id`; its only route to a lega is
    `league_competition`. Delete the competitions first and, inside the same
    transaction, the subquery sees them gone — the predicate empties and the fixtures
    become permanently unattributable to any lega.
    """
    from fantabot.adapters.persistence.repositories.league import LeagueRepository

    session = _RecordingSession(competition_ids=[311681, 177318])
    LeagueRepository(session).purge(4103937)

    kinds = [
        ("SELECT" if sql.lstrip().upper().startswith("SELECT") else "DELETE", sql)
        for sql in session.statements
    ]
    first_delete = next(i for i, (kind, _) in enumerate(kinds) if kind == "DELETE")
    assert kinds[first_delete][1].startswith("DELETE FROM league_fixture")
    # Every id lookup happened before any delete.
    assert all(kind == "SELECT" for kind, _ in kinds[:first_delete])

    order = [sql.split()[2] for kind, sql in kinds if kind == "DELETE"]
    assert order.index("league_fixture") < order.index("league_competition")


def test_purge_touches_only_lega_owned_tables() -> None:
    """Never the global reference tables the surviving lega still needs.

    `read_plan_inputs` builds an asta plan out of players, quotazioni, statistiche and
    player_sentiment. Deleting any of them on a disconnect would break the other lega.
    """
    from fantabot.adapters.persistence.repositories.league import LeagueRepository

    session = _RecordingSession(competition_ids=[311681])
    LeagueRepository(session).purge(4103937)

    deleted = {sql.split()[2] for sql in session.statements if sql.startswith("DELETE")}
    assert deleted == {
        "league_fixture",
        "league_competition",
        "league_custom_role",
        "league_player_pool",
        "league_team_snapshot",
        "league_snapshot",
    }
    # The token is the caller's job, removed last so a failure leaves a way back.
    assert "league_tokens" not in deleted
    for shared in ("players", "teams", "quotazioni", "statistiche", "player_sentiment", "asta"):
        assert shared not in deleted


def test_purge_skips_the_fixture_delete_when_the_lega_has_no_competitions() -> None:
    """An unbounded `IN ()` would otherwise be built from an empty id set."""
    from fantabot.adapters.persistence.repositories.league import LeagueRepository

    session = _RecordingSession(competition_ids=[])
    removed = LeagueRepository(session).purge(4103937)

    assert removed["league_fixture"] == 0
    assert not any(sql.startswith("DELETE FROM league_fixture") for sql in session.statements)


def test_purge_will_not_delete_a_competition_another_lega_claims() -> None:
    """Should never fire — the platform stamps one owning lega per competition.

    Kept because if it ever does fire, the alternative is silently deleting another
    lega's calendar.
    """
    from fantabot.adapters.persistence.repositories.league import LeagueRepository

    session = _RecordingSession(competition_ids=[311681])
    LeagueRepository(session).purge(4103937)

    fixtures = next(s for s in session.statements if s.startswith("DELETE FROM league_fixture"))
    assert "NOT IN" in fixtures.upper()
    assert "league_competition" in fixtures


class TestCorpusSummaryAnswersForEveryFormat:
    """The panel this feeds is the instrument every collection run is graded on.

    Both properties below are about what the *shape* of the answer must be, which is why
    they are here against a fake session rather than in the db tier: neither can be forced
    from data. A format that has never been collected has to read as zero — a missing row
    would render as "no data yet" for the one format whose emptiness is the finding.
    """

    def test_a_format_with_no_rows_reads_as_zero_rather_than_vanishing(self) -> None:
        from fantabot.adapters.persistence.models.aste import ASTA_TYPES
        from fantabot.adapters.persistence.repositories.aste import AsteRepository

        rows = AsteRepository(_session()).corpus_summary()

        assert [row.asta_type for row in rows] == list(ASTA_TYPES)
        assert [row.rooms for row in rows] == [0, 0]
        assert [row.events for row in rows] == [0, 0]
        assert [row.planner_sales for row in rows] == [0, 0]

    def test_the_league_shape_reaches_the_planner_filter(self) -> None:
        """8x500 is our room, not a law — `read_plan_inputs` records why that matters.

        A count whose filter is written in cannot answer for the riparazione in January
        or for a friend's league, and would disagree with `clearing_sales` the moment
        either is asked about.
        """
        from fantabot.adapters.persistence.repositories.aste import AsteRepository

        session = _session(literal=True)
        AsteRepository(session).corpus_summary(num_credits=250, num_teams=10)

        filtered = [sql for sql in session.statements if "FILTER" in sql.upper()]
        assert filtered, "no FILTER-ed count was built for the planner's own filter"
        assert any("250" in sql and "10" in sql for sql in filtered)


class TestLineupHistoryReads:
    """The lineup history's three rules, visible in the SQL each read issues: coach rows
    out, "before" cut by date, and the lega's scores taken from one capture only."""

    def test_no_players_or_no_seasons_issues_no_statement(self) -> None:
        from fantabot.adapters.persistence.repositories.lineup_history import (
            LineupHistoryRepository,
        )

        session = _session()
        repo = LineupHistoryRepository(session)

        assert repo.appearances([], seasons=["2025/26"], before=date(2026, 1, 1)) == []
        assert repo.appearances([1], seasons=[], before=date(2026, 1, 1)) == []
        assert repo.latest_sentiment([]) == {}
        assert session.statements == []

    def test_appearances_filter_on_ids_and_cut_by_date_not_giornata(self) -> None:
        from fantabot.adapters.persistence.repositories.lineup_history import (
            LineupHistoryRepository,
        )

        session = _session([], literal=True)
        LineupHistoryRepository(session).appearances(
            [6482], seasons=["2025/26"], before=date(2025, 12, 27)
        )

        (sql,) = session.statements
        assert "match_grain.player_id IN (6482)" in sql
        assert "match_grain.data < '2025-12-27'" in sql
        assert "giornata <" not in sql

    def test_the_lega_s_scores_come_from_its_latest_capture_and_only_calculated_rounds(
        self,
    ) -> None:
        """`league_competition` is append-only: one row per competition per sync. Joining it
        without choosing a capture multiplies every score by the number of syncs."""
        from fantabot.adapters.persistence.repositories.lineup_history import (
            LineupHistoryRepository,
        )

        session = _session([])
        LineupHistoryRepository(session).calculated_scores(4103937)

        (sql,) = session.statements
        assert "max(league_competition.captured_at)" in sql
        assert "league_competition.deleted IS false" in sql
        assert "league_fixture.calculated IS true" in sql

    def test_latest_sentiment_keeps_only_the_players_asked_for(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One `DISTINCT ON` read of every player, then filtered: 600 rows is cheaper than
        a round trip per roster player, and the ids come back as ints, not the reader's str."""
        from fantabot.adapters.persistence.repositories import sentiment
        from fantabot.adapters.persistence.repositories.lineup_history import (
            LineupHistoryRepository,
        )

        monkeypatch.setattr(
            sentiment.SentimentReadRepository, "all_latest", lambda self: {"1": "a", "2": "b"}
        )

        assert LineupHistoryRepository(_session()).latest_sentiment([2, 3]) == {2: "b"}


# -- T23: the backtest corpus read ------------------------------------------------------


class TestTheBacktestCorpusRead:
    def test_an_unknown_format_raises_before_any_statement_is_built(self) -> None:
        """An unknown format would otherwise come back as an empty corpus, which reads
        exactly like a corpus nobody recorded."""
        session = _session()

        with pytest.raises(ValueError, match="asta_type"):
            BacktestCorpusRepository(session).corpus_rows(asta_type="fantasy")

        assert session.statements == []

    def test_the_read_recovers_the_id_through_the_uuid_bridge(self) -> None:
        session = _session([], literal=True)

        BacktestCorpusRepository(session).corpus_rows()

        sql = session.statements[0]
        assert "coalesce" in sql.lower()
        assert "min(asta_assignment.fantacalcio_id)" in sql.lower()
        assert "group by asta_assignment.player_uuid" in sql.lower()

    def test_the_bridge_is_scoped_by_the_uuid_and_by_nothing_else(self) -> None:
        """A uuid is a platform-wide identity. Any extra key — the format, the room —
        makes a Mantra room's admission depend on what some other room happened to record,
        so the bridge's grouping is asserted **exhaustively** rather than by the absence of
        one column: a scope added by a different name would otherwise slip through."""
        session = _session([], literal=True)

        BacktestCorpusRepository(session).corpus_rows(asta_type="mantra")

        bridge, _sep, _rest = session.statements[0].partition(") AS bridge")
        _head, _sep2, grouping = bridge.rpartition("GROUP BY")
        assert grouping.strip() == "asta_assignment.player_uuid"
        _head2, _sep3, filtering = bridge.partition("WHERE")
        assert filtering.partition("GROUP BY")[0].strip() == (
            "asta_assignment.fantacalcio_id IS NOT NULL"
        )

    def test_unsold_lots_never_reach_the_fold(self) -> None:
        """29,406 of 192,197 assignment rows have no buyer — lots called and never bid on.
        Counted as a roster they would shrink every buyer below the minimum."""
        session = _session([], literal=True)

        BacktestCorpusRepository(session).corpus_rows()

        assert "buyer_team_id IS NOT NULL" in session.statements[0]

    def test_the_order_is_total(self) -> None:
        """Postgres has no inherent row order, and a corpus that comes back shuffled is a
        replay that differs from the one the gate graded."""
        session = _session([], literal=True)

        BacktestCorpusRepository(session).corpus_rows()

        _head, _sep, tail = session.statements[0].partition("ORDER BY")
        assert [part.strip() for part in tail.split(",")][:2] == [
            "asta.id", "asta_assignment.buyer_team_id"
        ]

    def test_one_room_row_per_room_and_one_sale_row_per_sale(self) -> None:
        rows = [
            ("a", 2, 500, 25, "buyerA", 132),
            ("a", 2, 500, 25, "buyerA", 574),
            ("a", 2, 500, 25, "buyerB", 6094),
            ("b", 8, 500, None, "buyerC", 2891),
        ]
        session = _session(rows)

        rooms, sales = BacktestCorpusRepository(session).corpus_rows()

        assert [(r.asta_id, r.num_teams, r.max_player) for r in rooms] == [
            ("a", 2, 25), ("b", 8, None)
        ]
        assert [(s.asta_id, s.buyer_team_id, s.player_id) for s in sales] == [
            ("a", "buyerA", 132), ("a", "buyerA", 574),
            ("a", "buyerB", 6094), ("b", "buyerC", 2891),
        ]


# -- T39: the shadow report's two reads --------------------------------------------------


class TestTheShadowRead:
    def test_the_fixture_read_carries_both_tids_and_our_points(self) -> None:
        """Both sides, not only the points: `apileague.match_detail` addresses a match by
        *both* tids, so the malus check (A18) cannot read anything without the opponent —
        and taking them from a second query is how two reads come to disagree about which
        match is being talked about."""

        class _Row:
            team_home, team_away = 10000003, 10000009
            points_home, points_away = 71.5, 64.0

        class _Scalars:
            def first(self) -> object:
                return _Row()

        class _Result:
            def scalars(self) -> object:
                return _Scalars()

        class _Session:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def execute(self, statement: object, *_a: object, **_k: object) -> object:
                self.statements.append(str(statement))
                return _Result()

        session = _Session()
        repo = ShadowRepository(session)  # type: ignore[arg-type]
        repo.competition_ids_for = lambda _league: [311681]  # type: ignore[method-assign]

        found = repo.fixture_for(4103937, matchday=4, tid=10000003)

        assert found == (10000003, 10000009, 71.5)

    def test_our_points_are_the_side_we_are_on(self) -> None:
        """Home and away are not interchangeable, and a report that read the wrong column
        would grade every away matchday against the opponent's total."""

        class _Row:
            team_home, team_away = 10000009, 10000003
            points_home, points_away = 71.5, 64.0

        class _Scalars:
            def first(self) -> object:
                return _Row()

        class _Result:
            def scalars(self) -> object:
                return _Scalars()

        class _Session:
            def execute(self, *_a: object, **_k: object) -> object:
                return _Result()

        repo = ShadowRepository(_Session())  # type: ignore[arg-type]
        repo.competition_ids_for = lambda _league: [311681]  # type: ignore[method-assign]

        assert repo.fixture_for(4103937, matchday=4, tid=10000003) == (
            10000009, 10000003, 64.0
        )

    def test_a_lega_with_no_competition_issues_no_second_statement(self) -> None:
        """`league_fixture` has no `league_id`; with no competition ids there is nothing to
        filter on, and `IN ()` is not a query worth sending."""

        class _Session:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self, *_a: object, **_k: object) -> object:
                self.calls += 1
                raise AssertionError("a statement was built with no competition")

        session = _Session()
        repo = ShadowRepository(session)  # type: ignore[arg-type]
        repo.competition_ids_for = lambda _league: []  # type: ignore[method-assign]

        assert repo.fixture_for(4103937, matchday=4, tid=1) is None
        assert session.calls == 0

    def test_the_appearances_read_refuses_coach_rows_and_the_unvoted(self) -> None:
        """Coach rows have no id and a row with no `voto_fc` is a player who did not take a
        vote — reading either as an appearance would field a man who never played."""
        session = _session([], literal=True)

        ShadowRepository(session).appearances_at("2026/27", 6)

        sql = session.statements[0]
        assert "match_grain.player_id IS NOT NULL" in sql
        assert "match_grain.voto_fc IS NOT NULL" in sql
