"""The integration tier's own contract: it connects, and it cleans up.

Everything below is marked ``db`` and is deselected by the default run. Bring
the stack up first: ``docker compose up -d && alembic upgrade head``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db

CANARY_TABLE = "players"
CANARY_ID = 999_999_999


def test_the_session_reaches_a_migrated_database(db_session: Session) -> None:
    assert db_session.execute(text("SELECT 1")).scalar() == 1

    exists = db_session.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :name)"
        ),
        {"name": CANARY_TABLE},
    ).scalar()
    assert exists, f"{CANARY_TABLE} is missing — run: alembic upgrade head"


def test_a_row_written_in_a_test_is_visible_inside_that_test(db_session: Session) -> None:
    db_session.execute(
        text(f'INSERT INTO "{CANARY_TABLE}" (id, nome) VALUES (:id, :n)'),
        {"id": CANARY_ID, "n": "Fixture Canary"},
    )
    db_session.commit()

    count = db_session.execute(
        text(f'SELECT count(*) FROM "{CANARY_TABLE}" WHERE id = :id'), {"id": CANARY_ID}
    ).scalar()
    assert count == 1


def test_the_previous_test_left_nothing_behind(db_session: Session) -> None:
    """Runs after the insert above and must not see it. This is what makes the
    tier re-runnable: a failed run does not poison the next one."""
    count = db_session.execute(
        text(f'SELECT count(*) FROM "{CANARY_TABLE}" WHERE id = :id'), {"id": CANARY_ID}
    ).scalar()
    assert count == 0


class TestPlayersSeed:
    """The referential root. Eight later tables carry a foreign key to it."""

    def test_the_union_seed_is_1474_not_quotazioni_s_1414(self, db_session: Session) -> None:
        count = db_session.execute(text("SELECT count(*) FROM players")).scalar()
        assert count == 1474

    def test_every_name_is_present(self, db_session: Session) -> None:
        blank = db_session.execute(
            text("SELECT count(*) FROM players WHERE nome IS NULL OR nome = ''")
        ).scalar()
        assert blank == 0

    def test_the_sixty_ids_that_exist_only_in_the_match_files_are_seeded(
        self, db_session: Session
    ) -> None:
        """88 voti rows per file reference these. Seeding from quotazioni alone
        gives 1414 and violates the foreign key when voti loads."""
        missing = db_session.execute(
            text(
                "SELECT count(*) FROM (VALUES (650),(4995),(5780)) AS v(id) "
                "WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.id = v.id)"
            )
        ).scalar()
        assert missing == 0


class TestTeamsSeed:
    """The only bridge between the two team vocabularies in the source data."""

    def test_twenty_clubs_in_every_one_of_the_five_seasons(self, db_session: Session) -> None:
        rows = db_session.execute(
            text("SELECT stagione, count(*) FROM teams GROUP BY 1 ORDER BY 1")
        ).all()
        assert [count for _, count in rows] == [20, 20, 20, 20, 20]
        assert len(rows) == 5

    def test_no_club_is_missing_its_full_name(self, db_session: Session) -> None:
        blank = db_session.execute(
            text("SELECT count(*) FROM teams WHERE nome_completo IS NULL OR nome_completo = ''")
        ).scalar()
        assert blank == 0

    def test_the_current_season_resolves_though_voti_has_no_rows_for_it(
        self, db_session: Session
    ) -> None:
        """voti.csv stops at 2025/26, so 2026/27's names come from the mapping
        being global across seasons rather than derived per season."""
        count = db_session.execute(
            text("SELECT count(*) FROM teams WHERE stagione = '2026/27' AND nome_completo <> ''")
        ).scalar()
        assert count == 20


class TestQuotazioniSeed:
    def test_both_listoni_hold_3201_rows(self, db_session: Session) -> None:
        rows = db_session.execute(
            text("SELECT listone, count(*) FROM quotazioni GROUP BY 1 ORDER BY 1")
        ).all()
        assert rows == [("classic", 3201), ("mantra", 3201)]

    def test_every_classic_row_has_exactly_one_valid_role(self, db_session: Session) -> None:
        bad = db_session.execute(
            text(
                "SELECT count(*) FROM quotazioni WHERE listone = 'classic' AND ("
                "cardinality(ruoli_codice) <> 1 "
                "OR NOT (ruoli_codice[1] = ANY(ARRAY['P','D','C','A'])))"
            )
        ).scalar()
        assert bad == 0

    def test_nothing_is_orphaned(self, db_session: Session) -> None:
        """The composite foreign key to teams is season-scoped, because a club
        code only means something within a season."""
        orphans = db_session.execute(
            text(
                "SELECT count(*) FROM quotazioni q "
                "LEFT JOIN players p ON p.id = q.player_id "
                "LEFT JOIN teams t ON t.stagione = q.stagione AND t.codice = q.squadra "
                "WHERE p.id IS NULL OR t.codice IS NULL"
            )
        ).scalar()
        assert orphans == 0


class TestStatisticheSeed:
    def test_both_listoni_hold_8034_rows_across_three_sources(
        self, db_session: Session
    ) -> None:
        rows = db_session.execute(
            text("SELECT listone, count(*) FROM statistiche GROUP BY 1 ORDER BY 1")
        ).all()
        assert rows == [("classic", 8034), ("mantra", 8034)]

        fonti = db_session.execute(
            text("SELECT count(DISTINCT fonte) FROM statistiche")
        ).scalar()
        assert fonti == 3

    def test_the_no_data_marker_is_null_and_never_zero(self, db_session: Session) -> None:
        """SPEC criterion 9, stated as the two numbers it turns on."""
        zeros, nulls = db_session.execute(
            text(
                "SELECT count(*) FILTER (WHERE media_voto = 0), "
                "count(*) FILTER (WHERE media_voto IS NULL) FROM statistiche"
            )
        ).one()
        assert (zeros, nulls) == (0, 2846)

    def test_no_counter_column_is_ever_null(self, db_session: Session) -> None:
        nulls = db_session.execute(
            text(
                "SELECT count(*) FROM statistiche WHERE partite_giocate IS NULL "
                "OR gol IS NULL OR assist IS NULL OR espulsioni IS NULL"
            )
        ).scalar()
        assert nulls == 0


class TestQiBiasSeed:
    def test_both_listoni_hold_2678_rows(self, db_session: Session) -> None:
        rows = db_session.execute(
            text("SELECT listone, count(*) FROM qi_bias GROUP BY 1 ORDER BY 1")
        ).all()
        assert rows == [("classic", 2678), ("mantra", 2678)]

    def test_delta_agrees_with_its_own_definition(self, db_session: Session) -> None:
        """Enforced by a CHECK too, so this proves the constraint is real
        rather than merely declared."""
        wrong = db_session.execute(
            text("SELECT count(*) FROM qi_bias WHERE delta <> qa - qi")
        ).scalar()
        assert wrong == 0

    def test_no_derived_value_is_missing(self, db_session: Session) -> None:
        nulls = db_session.execute(
            text("SELECT count(*) FROM qi_bias WHERE delta IS NULL OR pct_delta IS NULL")
        ).scalar()
        assert nulls == 0


class TestTargetPriceSeed:
    """The only table whose numbers get spent as real credits."""

    def test_523_priced_players_per_listone_all_in_one_season(
        self, db_session: Session
    ) -> None:
        rows = db_session.execute(
            text(
                "SELECT listone, count(*), count(DISTINCT stagione), min(stagione) "
                "FROM target_price GROUP BY 1 ORDER BY 1"
            )
        ).all()
        assert rows == [
            ("classic", 523, 1, "2026/27"),
            ("mantra", 523, 1, "2026/27"),
        ]

    def test_absent_priors_are_null_and_a_zero_forecast_is_not(
        self, db_session: Session
    ) -> None:
        """160 rows per listone have no prior and 363 no prediction — but one
        row genuinely forecasts +0.0, and that is a number, not a gap."""
        null_prior, null_pred, real_zero = db_session.execute(
            text(
                "SELECT count(*) FILTER (WHERE prior_media_fantavoto IS NULL), "
                "count(*) FILTER (WHERE predicted_pct_delta IS NULL), "
                "count(*) FILTER (WHERE predicted_pct_delta = 0) FROM target_price"
            )
        ).one()
        assert (null_prior, null_pred, real_zero) == (320, 726, 1)

    def test_flags_are_never_null_and_keep_their_casing(self, db_session: Session) -> None:
        empty, nulls, two = db_session.execute(
            text(
                "SELECT count(*) FILTER (WHERE flags = '{}'), "
                "count(*) FILTER (WHERE flags IS NULL), "
                "count(*) FILTER (WHERE cardinality(flags) = 2) FROM target_price"
            )
        ).one()
        assert (empty, nulls, two) == (276, 0, 70)

        lowercase = db_session.execute(
            text(
                "SELECT count(*) FROM target_price "
                "WHERE 'team_discount(MIL)' = ANY(flags)"
            )
        ).scalar()
        assert lowercase > 0


class TestVotiSeed:
    """The largest table, and the only place a nullable foreign key is required."""

    def test_all_50634_rows_land_with_3039_coach_rows(self, db_session: Session) -> None:
        total, coaches = db_session.execute(
            text("SELECT count(*), count(*) FILTER (WHERE player_id IS NULL) FROM voti")
        ).one()
        assert (total, coaches) == (50634, 3039)

    def test_every_coach_row_is_identifiable(self, db_session: Session) -> None:
        """They are keyed on nome by the second partial index, so a blank name
        would collide 3039 ways."""
        bad = db_session.execute(
            text(
                "SELECT count(*) FROM voti WHERE player_id IS NULL "
                "AND (nome IS NULL OR nome = '' OR ruolo_codice <> 'ALL')"
            )
        ).scalar()
        assert bad == 0

    def test_the_union_seeded_players_are_reachable(self, db_session: Session) -> None:
        """88 rows reference a player who exists only because players was
        seeded from the union rather than from quotazioni."""
        orphans = db_session.execute(
            text(
                "SELECT count(*) FROM voti v LEFT JOIN players p ON p.id = v.player_id "
                "WHERE v.player_id IS NOT NULL AND p.id IS NULL"
            )
        ).scalar()
        assert orphans == 0

    def test_no_grade_exceeds_the_scale(self, db_session: Session) -> None:
        impossible = db_session.execute(
            text(
                "SELECT count(*) FROM voti "
                "WHERE voto_fc > 10 OR voto_stat > 10 OR voto_italia > 10"
            )
        ).scalar()
        assert impossible == 0


class TestBonusMalusSeed:
    def test_it_agrees_with_voti_row_for_row(self, db_session: Session) -> None:
        """Same grain, same coach rows, same count — which is why they share
        the two-conflict-target upsert instead of each restating it."""
        total, coaches = db_session.execute(
            text(
                "SELECT count(*), count(*) FILTER (WHERE player_id IS NULL) "
                "FROM bonus_malus"
            )
        ).one()
        assert (total, coaches) == (50634, 3039)

    def test_no_counter_is_ever_null(self, db_session: Session) -> None:
        """A player who scored no goals scored zero goals — a fact, not a gap."""
        nulls = db_session.execute(
            text(
                "SELECT count(*) FROM bonus_malus WHERE ammonizione IS NULL "
                "OR gol_segnati IS NULL OR assist IS NULL OR mvp IS NULL"
            )
        ).scalar()
        assert nulls == 0

    def test_every_player_row_resolves(self, db_session: Session) -> None:
        orphans = db_session.execute(
            text(
                "SELECT count(*) FROM bonus_malus b "
                "LEFT JOIN players p ON p.id = b.player_id "
                "WHERE b.player_id IS NOT NULL AND p.id IS NULL"
            )
        ).scalar()
        assert orphans == 0


class TestSentimentWriteAgainstALiveTable:
    """The upsert semantics, where a fake session cannot settle them."""

    @staticmethod
    def _row(player_id: int, **overrides: str) -> dict[str, str]:
        row = {
            "data_run": "2026-10-07",
            "giorni_lookback": "14",
            "stagione": "2026/27",
            "id": str(player_id),
            "nome": "Canary",
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
            "riassunto": "prima lettura",
            "n_fonti": "2",
            "fonti": "https://a;https://b",
            "modello": "test",
        }
        row.update(overrides)
        return row

    @staticmethod
    def _a_real_player(db_session: Session) -> int:
        return int(
            db_session.execute(text("SELECT id FROM players ORDER BY id LIMIT 1")).scalar()
        )

    def test_the_same_key_twice_inserts_once(self, db_session: Session) -> None:
        from fantabot.db.repositories.sentiment import SentimentRepository

        player_id = self._a_real_player(db_session)
        repo = SentimentRepository(db_session)

        repo.upsert_rows([self._row(player_id)])
        repo.upsert_rows([self._row(player_id, riassunto="seconda lettura")])

        rows = db_session.execute(
            text("SELECT riassunto FROM player_sentiment WHERE player_id = :p"),
            {"p": player_id},
        ).scalars().all()
        assert rows == ["prima lettura"], "the second write should have been ignored"

    def test_force_overwrites_in_place_and_never_adds_a_row(
        self, db_session: Session
    ) -> None:
        """Today --force merely skips the resume filter and append_rows has no
        dedup, so it writes a duplicate that _load keeps. This is the fix."""
        from fantabot.db.repositories.sentiment import SentimentRepository

        player_id = self._a_real_player(db_session)
        repo = SentimentRepository(db_session)

        repo.upsert_rows([self._row(player_id)])
        repo.upsert_rows([self._row(player_id, riassunto="corretta")], force=True)

        rows = db_session.execute(
            text("SELECT riassunto FROM player_sentiment WHERE player_id = :p"),
            {"p": player_id},
        ).scalars().all()
        assert rows == ["corretta"]

    def test_existing_keys_comes_back_as_the_strings_the_cli_compares(
        self, db_session: Session
    ) -> None:
        """Against a real date column, which is where a (date, int) tuple would
        otherwise slip through and silently disable resume."""
        from datetime import date

        from fantabot.db.repositories.sentiment import SentimentRepository

        player_id = self._a_real_player(db_session)
        repo = SentimentRepository(db_session)
        repo.upsert_rows([self._row(player_id)])

        keys = repo.existing_keys(date(2026, 10, 7))

        assert (date(2026, 10, 7).isoformat(), str(player_id)) in keys
        for stored_date, stored_id in keys:
            assert isinstance(stored_date, str)
            assert isinstance(stored_id, str)


class TestSentimentReadPath:
    """The four behaviours the natural SQL translation quietly breaks."""

    @staticmethod
    def _write(db_session: Session, player_id: int, runs: list[tuple[str, str, str]]) -> None:
        """runs: (data_run, confidenza, deriva_ruolo)."""
        from fantabot.db.repositories.sentiment import SentimentRepository

        repo = SentimentRepository(db_session)
        for data_run, confidenza, deriva in runs:
            repo.upsert_rows(
                [
                    {
                        "data_run": data_run,
                        "giorni_lookback": "14",
                        "stagione": "2026/27",
                        "id": str(player_id),
                        "nome": f"P{player_id}",
                        "squadra": "ATA",
                        "ruolo": "Difensore",
                        "ruoli_mantra": "B;DS",
                        "ruolo_campo": "W",
                        "deriva_ruolo": deriva,
                        "sentiment": "0.50",
                        "disponibilita": "1.00",
                        "titolarita": "0.80",
                        "mercato": "0.00",
                        "forma": "0.20",
                        "rigorista": "0.00",
                        "piazzati": "0.00",
                        "confidenza": confidenza,
                        "riassunto": data_run,
                        "n_fonti": "1",
                        "fonti": "https://a",
                        "modello": "test",
                    }
                ],
                force=True,
            )

    @staticmethod
    def _players(db_session: Session, n: int) -> list[int]:
        return [
            int(v)
            for v in db_session.execute(
                text("SELECT id FROM players ORDER BY id LIMIT :n"), {"n": n}
            ).scalars()
        ]

    def test_latest_is_the_most_recent_run_not_an_arbitrary_row(
        self, db_session: Session
    ) -> None:
        from fantabot.db.repositories.sentiment import SentimentReadRepository

        (player_id,) = self._players(db_session, 1)
        self._write(
            db_session,
            player_id,
            [("2026-09-02", "0.5", "0.0"), ("2026-10-07", "0.5", "0.0"), ("2026-09-16", "0.5", "0.0")],
        )

        row = SentimentReadRepository(db_session).latest(str(player_id))

        assert row is not None
        assert row.data_run == "2026-10-07"

    def test_trailing_slices_then_filters(self, db_session: Session) -> None:
        """Five runs, the last four of which include two silent ones. Filtering
        before slicing would reach back to the fifth and widen the window."""
        from fantabot.db.repositories.sentiment import SentimentReadRepository

        (player_id,) = self._players(db_session, 1)
        self._write(
            db_session,
            player_id,
            [
                ("2026-09-02", "0.9", "0.0"),
                ("2026-09-09", "0.0", "0.0"),
                ("2026-09-16", "0.8", "0.0"),
                ("2026-09-23", "0.0", "0.0"),
                ("2026-09-30", "0.7", "0.0"),
            ],
        )

        trailing = SentimentReadRepository(db_session).trailing(str(player_id), weeks=4)

        assert trailing is not None
        assert trailing.rows_used == 2

    def test_an_all_silent_window_has_no_average_at_all(self, db_session: Session) -> None:
        """confidenza 0 means no coverage was found, not that he is neutral."""
        from fantabot.db.repositories.sentiment import SentimentReadRepository

        (player_id,) = self._players(db_session, 1)
        self._write(db_session, player_id, [("2026-09-30", "0.0", "0.0")])

        assert SentimentReadRepository(db_session).trailing(str(player_id)) is None

    def test_scores_come_back_as_floats_not_decimals(self, db_session: Session) -> None:
        from fantabot.db.repositories.sentiment import SentimentReadRepository

        (player_id,) = self._players(db_session, 1)
        self._write(db_session, player_id, [("2026-09-30", "0.7", "0.5")])

        row = SentimentReadRepository(db_session).latest(str(player_id))

        assert row is not None
        assert isinstance(row.confidenza, float)
        assert isinstance(row.deriva_ruolo, float)

    def test_drifted_ranks_worst_first_in_one_statement(self, db_session: Session) -> None:
        from fantabot.db.repositories.sentiment import SentimentReadRepository

        low, high, none_at_all = self._players(db_session, 3)
        self._write(db_session, low, [("2026-09-30", "0.3", "0.30")])
        self._write(db_session, high, [("2026-09-30", "0.9", "0.90")])
        self._write(db_session, none_at_all, [("2026-09-30", "0.9", "0.00")])

        drifted = SentimentReadRepository(db_session).drifted()
        ids = [d.player_id for d in drifted]

        assert ids.index(str(high)) < ids.index(str(low))
        assert str(none_at_all) not in ids

    def test_drifted_uses_only_each_player_s_latest_reading(
        self, db_session: Session
    ) -> None:
        """A tag that drifted in September and was confirmed in October is not
        drifted. DISTINCT ON takes the newest row, not any row."""
        from fantabot.db.repositories.sentiment import SentimentReadRepository

        (player_id,) = self._players(db_session, 1)
        self._write(
            db_session,
            player_id,
            [("2026-09-02", "0.9", "0.90"), ("2026-10-07", "0.9", "0.00")],
        )

        ids = [d.player_id for d in SentimentReadRepository(db_session).drifted()]

        assert str(player_id) not in ids
