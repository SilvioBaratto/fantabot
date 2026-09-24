"""`LineupHistoryRepository` against a real Postgres. Marked ``db``; one test ``dbdata``.

Every ``db`` test seeds synthetic rows only — a season (`1999/00`), players and a lega
that cannot exist — so it asserts on what it wrote and nothing else `fantabot_test` holds.
The one ``dbdata`` test reads the recorded seasons, inside the same rolled-back
transaction, because "the formula reproduces `fantavoto_fc`" is a claim about 48k real
rows and no fixture can stand in for them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from conftest import make_synthetic_players
from sqlalchemy import text
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.repositories.lineup_history import LineupHistoryRepository
from fantabot.domain.lineup.scoring import ScoringRules, lega_fantavoto

pytestmark = pytest.mark.db

SEASON = "1999/00"
LEAGUE_ID = 999_999_101
ACTIVE, DELETED = 999_700_001, 999_700_002

#: Lega 4103937's 24 calculated scores (rounds 1-3), reused as the synthetic lega's.
SCORES = [
    (82.0, 73.0), (68.5, 65.5), (82.5, 77.0), (66.5, 72.5), (73.0, 80.0), (74.0, 68.0),
    (77.0, 70.5), (64.0, 68.5), (88.0, 77.5), (72.0, 64.5), (76.0, 61.0), (65.5, 75.0),
]


def _row(player_id: int | None, giornata: int, played_on: date, home: str, away: str,
         **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "stagione": SEASON, "giornata": giornata, "data": played_on,
        "squadra_raw": home, "avversario_raw": away, "gol_squadra": 1, "gol_avversario": 0,
        "player_id": player_id, "nome": f"synthetic-{player_id}", "ruolo_codice": "D",
        "ruolo": "Difensore", "voto_fc": 6.0, "fantavoto_fc": 6.0,
        "ammonizione": 0, "espulsione": 0, "gol_segnati": 0, "gol_subiti": 0, "autoreti": 0,
        "rigori_segnati": 0, "rigori_sbagliati": 0, "rigori_parati": 0, "assist": 0, "mvp": 0,
    }
    row.update(over)
    return row


def _insert_grain(session: Session, rows: list[dict[str, Any]]) -> None:
    columns = ", ".join(rows[0])
    values = ", ".join(f":{c}" for c in rows[0])
    session.execute(
        text(f"INSERT INTO match_grain ({columns}) VALUES ({values})"),
        rows,
    )


def _players(session: Session, count: int) -> list[int]:
    return [int(i) for i in make_synthetic_players(session, count)]


class TestAppearances:
    def test_only_the_seasons_asked_for(self, db_session: Session) -> None:
        (player,) = _players(db_session, 1)
        _insert_grain(db_session, [
            _row(player, 1, date(1999, 9, 1), "Synthetic FC", "Other FC"),
            _row(player, 1, date(1998, 9, 1), "Synthetic FC", "Other FC", stagione="1998/99"),
        ])

        rows = LineupHistoryRepository(db_session).appearances(
            [player], seasons=[SEASON], before=date(2000, 1, 1)
        )

        assert [r.fixture.season for r in rows] == [SEASON]

    def test_a_row_with_no_vote_is_not_an_appearance(self, db_session: Session) -> None:
        """None exist today; the column allows one, and scoring it would invent a 0."""
        voted, silent = _players(db_session, 2)
        _insert_grain(db_session, [
            _row(voted, 2, date(1999, 9, 10), "Synthetic FC", "Other FC"),
            _row(silent, 2, date(1999, 9, 10), "Synthetic FC", "Other FC",
                 voto_fc=None, fantavoto_fc=None),
        ])

        rows = LineupHistoryRepository(db_session).appearances(
            [voted, silent], seasons=[SEASON], before=date(2000, 1, 1)
        )

        assert [r.player_id for r in rows] == [voted]

    def test_the_cutoff_is_by_date_and_a_giornata_s_opening_day_is_not_history(
        self, db_session: Session
    ) -> None:
        regular, postponed, opening = _players(db_session, 3)
        _insert_grain(db_session, [
            _row(regular, 16, date(1999, 12, 20), "Juventus", "Roma"),
            _row(postponed, 16, date(2000, 1, 14), "Inter", "Lecce"),  # giornata 16, played late
            _row(opening, 17, date(1999, 12, 27), "Lecce", "Como"),  # giornata 17's first day
        ])

        rows = LineupHistoryRepository(db_session).appearances(
            [regular, postponed, opening], seasons=[SEASON], before=date(1999, 12, 27)
        )

        assert [r.player_id for r in rows] == [regular]

    def test_coach_rows_are_not_players(self, db_session: Session) -> None:
        (player,) = _players(db_session, 1)
        _insert_grain(db_session, [
            _row(player, 1, date(1999, 9, 1), "Synthetic FC", "Other FC"),
            _row(None, 1, date(1999, 9, 1), "Synthetic FC", "Other FC",
                 nome="synthetic-coach", ruolo_codice="ALL", ruolo="Allenatore"),
        ])

        rows = LineupHistoryRepository(db_session).appearances(
            [player], seasons=[SEASON], before=date(2000, 1, 1)
        )

        assert [(r.player_id, r.role) for r in rows] == [(player, "D")]

    def test_a_row_carries_its_match_and_its_components(self, db_session: Session) -> None:
        """`squadra_raw` is the home team, whichever side the player was on."""
        (player,) = _players(db_session, 1)
        _insert_grain(db_session, [
            _row(player, 3, date(1999, 9, 20), "Udinese", "Lazio", gol_squadra=1,
                 gol_avversario=2, voto_fc=6.5, fantavoto_fc=12.5, rigori_parati=1, mvp=1),
        ])

        (row,) = LineupHistoryRepository(db_session).appearances(
            [player], seasons=[SEASON], before=date(2000, 1, 1)
        )

        assert (row.fixture.home, row.fixture.away) == ("Udinese", "Lazio")
        assert (row.fixture.home_goals, row.fixture.away_goals) == (1, 2)
        assert (row.scored.voto_fc, row.scored.rigori_parati, row.scored.mvp) == (6.5, 1, 1)
        assert row.fantavoto_fc == 12.5


def test_fixtures_are_the_season_s_matches_once_each(db_session: Session) -> None:
    """Two players in one match are one fixture, not two."""
    a, b = _players(db_session, 2)
    _insert_grain(db_session, [
        _row(a, 5, date(1999, 10, 3), "Bologna", "Lazio"),
        _row(b, 5, date(1999, 10, 3), "Bologna", "Lazio"),
    ])

    fixtures = LineupHistoryRepository(db_session).fixtures(SEASON)

    assert [(f.giornata, f.home, f.away) for f in fixtures] == [(5, "Bologna", "Lazio")]


def test_roles_and_valuations_are_separate_reads(db_session: Session) -> None:
    (player,) = _players(db_session, 1)
    db_session.execute(  # `quotazioni.squadra` is a foreign key into the season's teams
        text("INSERT INTO teams (stagione, codice, nome_completo) VALUES (:s, 'LAZ', 'Lazio')"),
        {"s": SEASON},
    )
    db_session.execute(  # the same player in both listoni: roles and `qi` differ by listone
        text("INSERT INTO quotazioni (stagione, player_id, listone, squadra, ruoli_codice, "
             "ruoli, qi, qa, fvm) VALUES "
             "(:s, :p, 'mantra', 'LAZ', ARRAY['DC','DD'], ARRAY['Dc','Dd'], 12, 14, 30), "
             "(:s, :p, 'classic', 'LAZ', ARRAY['d'], ARRAY['D'], 9, 10, 20)"),
        {"s": SEASON, "p": player},
    )
    repo = LineupHistoryRepository(db_session)

    assert repo.roles(SEASON)[player] == ("DC", "DD")
    valuation = repo.valuations(SEASON)[player]
    assert (valuation.qi, valuation.squadra) == (12, "LAZ")


def test_the_lega_s_scores_are_24_whatever_the_capture_count(db_session: Session) -> None:
    """stats-12: three syncs, each recording both competitions. The second was live in the
    two older captures and is deleted in the latest one — so only a read that takes the
    latest capture leaves its scores out. Uncalculated rounds carry zeros and nulls."""
    now = datetime.now(UTC)
    for days_ago in (17, 11, 1):
        for competition, deleted in ((ACTIVE, False), (DELETED, days_ago == 1)):
            db_session.execute(
                # `nome` and `team_ids` were dropped from this table on 2026-09-24 —
                # written every sync, read by nothing. `deleted` is the column this test
                # is actually about, and it stays.
                text("INSERT INTO league_competition (captured_at, league_id, competition_id, "
                     "deleted) VALUES (:t, :l, :c, :d)"),
                {"t": now - timedelta(days=days_ago), "l": LEAGUE_ID, "c": competition,
                 "d": deleted},
            )
    fixtures: list[dict[str, Any]] = [
        {"c": ACTIVE, "md": 1 + i // 4, "h": 10 + 2 * i, "a": 11 + 2 * i,
         "ph": home, "pa": away, "calc": True}
        for i, (home, away) in enumerate(SCORES)
    ]
    fixtures += [
        {"c": ACTIVE, "md": 4, "h": 90, "a": 91, "ph": 0.0, "pa": 0.0, "calc": False},
        {"c": ACTIVE, "md": 4, "h": 92, "a": 93, "ph": None, "pa": None, "calc": False},
        {"c": DELETED, "md": 1, "h": 94, "a": 95, "ph": 99.0, "pa": 98.0, "calc": True},
    ]
    db_session.execute(
        text("INSERT INTO league_fixture (competition_id, matchday, team_home, team_away, "
             "points_home, points_away, calculated) VALUES (:c, :md, :h, :a, :ph, :pa, :calc)"),
        fixtures,
    )

    scores = LineupHistoryRepository(db_session).calculated_scores(LEAGUE_ID)

    assert sorted(scores) == sorted(p for pair in SCORES for p in pair)


@pytest.mark.dbdata
def test_the_formula_reproduces_fantavoto_fc_on_the_recorded_seasons(
    db_session: Session,
) -> None:
    """SPEC A8's "exact on 99.66%", through the repository the projection will use."""
    ids = db_session.execute(
        text("SELECT DISTINCT player_id FROM match_grain WHERE player_id IS NOT NULL")
    ).scalars().all()
    seasons = db_session.execute(text("SELECT DISTINCT stagione FROM match_grain")).scalars().all()
    fc_default = ScoringRules(
        goal=3.0, assist=1.0, yellow=-0.5, red=-1.0, own_goal=-2.0, penalty_scored=3.0,
        penalty_missed=-3.0, penalty_saved=3.0, conceded=-1.0, motm=0.0, clean_sheet=0.0,
        decisive_goal=0.0, threshold=66.0, steps=(),
    )

    rows = LineupHistoryRepository(db_session).appearances(
        ids, seasons=seasons, before=date(2100, 1, 1)
    )
    exact = sum(
        1
        for r in rows
        if r.fantavoto_fc is not None
        and abs(
            lega_fantavoto(r.scored, rules=fc_default, season=r.fixture.season, role=r.role)
            - r.fantavoto_fc
        ) < 0.01
    )

    assert len(rows) > 45_000
    assert exact / len(rows) >= 0.996, f"{exact} of {len(rows)}"
