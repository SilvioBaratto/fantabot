"""`LeagueRepository.record_team_snapshot` against a real Postgres. Marked ``db``.

**Why not "call it twice and count two rows"**, the shape every other repository test
here uses. `league_team_snapshot`'s primary key includes `captured_at`, whose column
default is `func.now()` — Postgres's transaction-timestamp, frozen for the whole
transaction `db_session` wraps a test in, not the statement-timestamp. Two inserts in
one test would collide on the same `captured_at` and raise `IntegrityError`, which
would be this test file failing on Postgres's own semantics, not on anything
`record_team_snapshot` gets wrong. A real CLI run opens a fresh session per invocation
and never hits this; only two calls sharing one transaction would.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from fantabot.adapters.persistence.models.league import LeagueTeamSnapshot
from fantabot.adapters.persistence.repositories.league import LeagueRepository
from fantabot.domain.shared.league import TeamSnapshot

pytestmark = pytest.mark.db

#: Far above any real lega/team id, so this file cannot collide with production rows.
LEAGUE_ID = 999999001
TEAM_ID = 999999002


def _rows(session: Session) -> list[LeagueTeamSnapshot]:
    return list(
        session.execute(
            select(LeagueTeamSnapshot).where(
                LeagueTeamSnapshot.league_id == LEAGUE_ID,
                LeagueTeamSnapshot.team_id == TEAM_ID,
            )
        ).scalars()
    )


def test_recording_a_snapshot_inserts_the_row_with_every_field(db_session: Session) -> None:
    snapshot = TeamSnapshot(
        league_id=LEAGUE_ID, team_id=TEAM_ID, user_id=20000003,
        nome="Team C", owner="Owner C",
        credits_initial=500, credits_spent=474, credits_remaining=26,
    )

    LeagueRepository(db_session).record_team_snapshot(snapshot)
    db_session.flush()

    rows = _rows(db_session)
    assert len(rows) == 1
    assert rows[0].nome == "Team C"
    assert rows[0].owner == "Owner C"
    assert rows[0].credits_spent == 474
    assert rows[0].credits_remaining == 26


def test_a_new_capture_is_appended_alongside_an_older_one_not_over_it(
    db_session: Session,
) -> None:
    """Seeded directly with its own `captured_at`, in the past, so this test does not
    need a second call to `record_team_snapshot` inside the one transaction `db_session`
    wraps it in — see this module's docstring for why that would collide."""
    earlier = datetime(2026, 8, 26, tzinfo=UTC) - timedelta(days=1)
    db_session.execute(
        insert(LeagueTeamSnapshot).values(
            captured_at=earlier, league_id=LEAGUE_ID, team_id=TEAM_ID,
            user_id=None, nome="Team C", owner="Owner C",
            credits_initial=500, credits_spent=0, credits_remaining=500,
        )
    )
    db_session.flush()

    snapshot = TeamSnapshot(
        league_id=LEAGUE_ID, team_id=TEAM_ID, user_id=20000003,
        nome="Team C", owner="Owner C",
        credits_initial=500, credits_spent=474, credits_remaining=26,
    )
    LeagueRepository(db_session).record_team_snapshot(snapshot)
    db_session.flush()

    rows = sorted(_rows(db_session), key=lambda r: r.captured_at)
    assert len(rows) == 2, "the seeded row must survive, not be overwritten"
    assert rows[0].credits_spent == 0, "the earlier capture is untouched"
    assert rows[1].credits_spent == 474, "the new capture landed as its own row"
