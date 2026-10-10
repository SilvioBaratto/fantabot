"""``updated_at`` has to move when an ``ON CONFLICT`` upsert replaces a row.

``Column.onupdate`` is applied to UPDATE statements only; an ``INSERT ... ON CONFLICT DO
UPDATE`` writes exactly its ``set_``. Statements are compiled, never executed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from fantabot.adapters.persistence import scraping
from fantabot.adapters.persistence.models.matches import MatchGrain
from fantabot.adapters.persistence.repositories.league import LeagueRepository
from fantabot.adapters.persistence.repositories.reference import ReferenceRepository
from fantabot.adapters.persistence.upserts import upsert_two_passes
from fantabot.domain.lega.models import Fixture


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: Any, params: Any = None) -> Any:
        if hasattr(statement, "compile"):
            sql = str(statement.compile(dialect=postgresql.dialect()))
        else:
            sql = str(statement)
        self.statements.append(sql)
        return None


def _updating(sql: str) -> str:
    return sql.split("DO UPDATE SET", 1)[1] if "DO UPDATE SET" in sql else ""


def _assert_stamps(statements: list[str]) -> None:
    updating = [s for s in statements if "DO UPDATE SET" in s]
    assert updating
    for sql in updating:
        assert "updated_at" in _updating(sql).lower(), sql


def test_fixture_upsert_stamps_updated_at() -> None:
    session = _Recorder()
    fixture = Fixture(
        competition_id=1, matchday=1, team_home=1, team_away=2, championship_matchday=1,
        points_home=None, points_away=None, standing_home=None, standing_away=None,
        result=None, real_result=None, calculated=False,
    )
    LeagueRepository(session).upsert_fixtures([fixture])  # type: ignore[arg-type]
    _assert_stamps(session.statements)


def test_match_grain_upsert_stamps_updated_at() -> None:
    session = _Recorder()
    rows = [{"player_id": 1, "stagione": "2026/27", "giornata": 1, "nome": "x", "voto_fc": 6}]
    upsert_two_passes(session, MatchGrain, rows, updatable=("voto_fc",))  # type: ignore[arg-type]
    _assert_stamps(session.statements)


def test_exclusion_upsert_stamps_updated_at() -> None:
    session = _Recorder()
    ReferenceRepository(session).exclude_player(1, reason="r")  # type: ignore[arg-type]
    _assert_stamps(session.statements)


def test_raw_scraper_upserts_stamp_updated_at() -> None:
    for call in (scraping.upsert_quotazioni, scraping.upsert_statistiche):
        _check_raw(call)


def _check_raw(call: Any) -> None:
    session = _Recorder()
    call(session, [{"player_id": 1, "nome": "n", "stagione": "s", "squadra": "T"}])
    own = [
        s for s in session.statements
        if "DO UPDATE SET" in s and ("INTO quotazioni" in s or "INTO statistiche" in s)
    ]
    assert own and all("updated_at" in _updating(s) for s in own)


def test_players_dimension_upsert_stamps_updated_at() -> None:
    session = _Recorder()
    row = {"player_id": 1, "nome": "n", "stagione": "s", "squadra": "T"}
    scraping.upsert_quotazioni(session, [row])
    players = [s for s in session.statements if "INTO players" in s]
    assert players and "updated_at" in _updating(players[0])


def test_target_price_upsert_stamps_updated_at() -> None:
    session = _Recorder()
    scraping.upsert_target_price(
        session, "mantra", "2026/27", [{"player_id": 1, "role": "A", "flags": ""}]
    )
    own = [s for s in session.statements if "INTO target_price" in s]
    assert own and "updated_at" in _updating(own[0])



def test_sentiment_force_upsert_stamps_updated_at(monkeypatch: pytest.MonkeyPatch) -> None:
    import fantabot.adapters.persistence.repositories.sentiment as mod

    monkeypatch.setattr(mod, "to_record", lambda row: {"data_run": "r", "player_id": 1})
    session = _Recorder()
    mod.SentimentRepository(session).upsert_rows([{}], force=True)  # type: ignore[arg-type]
    _assert_stamps(session.statements)


def test_pg_hba_rejection_is_not_a_missing_database() -> None:
    from fantabot.adapters.persistence.repositories.admin import classify_failure

    class _Dbapi(Exception):
        pass

    message = (
        'FATAL:  no pg_hba.conf entry for host "10.0.0.1", user "u", database "fantabot", '
        "no encryption"
    )
    assert classify_failure(OperationalError("SELECT 1", {}, _Dbapi(message))) == "credentials"


class _Cursor:
    def all(self) -> list[tuple[Any, ...]]:
        return [
            (1, "2025/26", 0, None),
            (1, "2025/26", 30, 6.5),
            (1, "2025/26", 28, 7.0),
        ]


class _Reader:
    def execute(self, *args: Any, **kwargs: Any) -> _Cursor:
        return _Cursor()


def test_prior_appearances_come_from_a_row_that_has_a_mean() -> None:
    prior = scraping.load_prior_stats(_Reader())  # type: ignore[arg-type]

    assert prior[("1", "2025/26")].partite_giocate == 30


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.rowcount = 1

    def scalars(self) -> list[Any]:
        return self._rows

    def all(self) -> list[Any]:
        return self._rows


class _TeamSession(_Recorder):
    def execute(self, statement: Any, params: Any = None) -> Any:
        super().execute(statement, params)
        sql = self.statements[-1]
        if "INSERT INTO teams" in sql or "ON CONFLICT" in sql:
            return _Rows([])
        if "squadra_raw" in sql:
            return _Rows(["Juventus"])
        return _Rows([("2026/27", "JUV")])


def test_team_name_backfill_stamps_updated_at() -> None:
    session = _TeamSession()
    ReferenceRepository(session).backfill_team_names()  # type: ignore[arg-type]
    _assert_stamps(session.statements)


def test_fantalab_session_upsert_stamps_updated_at() -> None:
    from fantabot.adapters.persistence.repositories.tokens import FantalabSessionRepository

    session = _Recorder()
    FantalabSessionRepository(session).upsert(  # type: ignore[arg-type]
        user_id="u", ciphertext=b"x", fingerprint="f", at=datetime.now(tz=UTC)
    )
    _assert_stamps(session.statements)
