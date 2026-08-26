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
