"""The integration tier's own contract: it connects, and it cleans up.

Everything below is marked ``db`` and is deselected by the default run. Bring
the stack up first: ``docker compose up -d && alembic upgrade head``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db

PROBE = "_probe_match_grain"


def test_the_session_reaches_a_migrated_database(db_session: Session) -> None:
    assert db_session.execute(text("SELECT 1")).scalar() == 1

    exists = db_session.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :name)"
        ),
        {"name": PROBE},
    ).scalar()
    assert exists, f"{PROBE} is missing — run: alembic upgrade head"


def test_a_row_written_in_a_test_is_visible_inside_that_test(db_session: Session) -> None:
    db_session.execute(
        text(
            f'INSERT INTO "{PROBE}" (stagione, giornata, player_id, nome, ruoli_codice) '
            "VALUES ('2026/27', 1, 999999, 'Fixture Canary', ARRAY['P'])"
        )
    )
    db_session.commit()

    count = db_session.execute(
        text(f'SELECT count(*) FROM "{PROBE}" WHERE nome = :n'), {"n": "Fixture Canary"}
    ).scalar()
    assert count == 1


def test_the_previous_test_left_nothing_behind(db_session: Session) -> None:
    """Runs after the insert above and must not see it. This is what makes the
    tier re-runnable: a failed run does not poison the next one."""
    count = db_session.execute(
        text(f'SELECT count(*) FROM "{PROBE}" WHERE nome = :n'), {"n": "Fixture Canary"}
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
