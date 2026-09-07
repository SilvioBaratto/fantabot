"""Reading a lega back out of its snapshots — one implementation, two surfaces.

`interface/lega.py::_show` and `api/reads/league.py` hand-wrote SQLAlchemy over the same
two models, in two places, with no shared function. The app's existed for a real reason:
`LeagueRepository` is write-only, because the `league_*` tables are append-only snapshots
and the point of them is the drift — the roster-rules change of 2026-09-02 is only knowable
because two captures disagree, and an upsert would have erased it. That is a reason for a
read helper. It is not a reason for a *second* one.

**Why this is the failure `GET /asta/plan` had, one step earlier.** Two implementations of
"the lega's latest capture" cannot be checked against each other, so the day one of them
learns something — a new table, a corrected `league_fixture` join — the other silently
keeps answering the old question. That is exactly how the page came to plan on inputs that
differed from the command's in ten places.

**The `league_fixture` join is the part worth having in one place.** A fixture has no
`league_id` at all; its only route to a lega is through `league_competition.competition_id`,
and `_show` used to count it with **no `WHERE` clause**, under a comment claiming it counted
"this lega's competitions". Harmless while one lega owned every row, and wrong the moment a
second exists or one is disconnected — the survivor's calendar would be reported as the
other's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fantabot.adapters.persistence.models.league import (
    LeagueCompetition,
    LeagueCustomRole,
    LeagueFixture,
    LeaguePlayerPool,
    LeagueSnapshot,
    LeagueTeamSnapshot,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: The five snapshot tables, in the order an operator reads them: the lega's own settings,
#: then the teams, then what hangs off them. Named once so a new table is added here rather
#: than in whichever surface noticed it first.
SNAPSHOT_TABLES: tuple[tuple[str, Any], ...] = (
    ("league_snapshot", LeagueSnapshot),
    ("league_team_snapshot", LeagueTeamSnapshot),
    ("league_competition", LeagueCompetition),
    ("league_custom_role", LeagueCustomRole),
    ("league_player_pool", LeaguePlayerPool),
)


@dataclass(frozen=True, slots=True)
class CaptureRow:
    """One table's freshness and size at its most recent capture."""

    table: str
    #: `None` when the table has never been written for this lega — "mai", not zero rows.
    #: The two are different facts and a count alone cannot tell them apart.
    captured_at: datetime | None
    rows: int


def all_league_ids(session: Session) -> list[int]:
    """Every league_id with at least one snapshot, ascending."""
    from sqlalchemy import select

    stmt = select(LeagueSnapshot.league_id).distinct().order_by(LeagueSnapshot.league_id)
    return list(session.execute(stmt).scalars().all())


def latest_capture(session: Session, model: Any, league_id: int) -> datetime | None:
    """When this table was last written for this lega, or `None`."""
    from sqlalchemy import func, select

    captured: datetime | None = session.execute(
        select(func.max(model.captured_at)).where(model.league_id == league_id)
    ).scalar()
    return captured


def latest_settings(session: Session, league_id: int) -> LeagueSnapshot | None:
    """The most recent `LeagueSnapshot` for one lega, or `None`."""
    from sqlalchemy import select

    stmt = (
        select(LeagueSnapshot)
        .where(LeagueSnapshot.league_id == league_id)
        .order_by(LeagueSnapshot.captured_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def latest_rosters(session: Session, league_id: int) -> list[LeagueTeamSnapshot]:
    """Every team's snapshot at the lega's most recent capture, ordered by `team_id`."""
    from sqlalchemy import select

    last = latest_capture(session, LeagueTeamSnapshot, league_id)
    if last is None:
        return []
    stmt = (
        select(LeagueTeamSnapshot)
        .where(
            LeagueTeamSnapshot.league_id == league_id,
            LeagueTeamSnapshot.captured_at == last,
        )
        .order_by(LeagueTeamSnapshot.team_id)
    )
    return list(session.execute(stmt).scalars().all())


def capture_inventory(session: Session, league_id: int) -> list[CaptureRow]:
    """What is stored for one lega, per table, and when — `lega show`'s whole answer.

    `league_fixture` is last and is not a snapshot: it upserts and has no `captured_at`, so
    its freshness is the newest `updated_at`. Counting it separately rather than skipping
    it is deliberate — the calendar is the only place results appear, and a `show` that
    silently omitted it would read as "nothing was synced".
    """
    from sqlalchemy import func, select

    rows: list[CaptureRow] = []
    for name, model in SNAPSHOT_TABLES:
        last = latest_capture(session, model, league_id)
        count = 0
        if last is not None:
            count = session.execute(
                select(func.count())
                .select_from(model)
                .where(model.league_id == league_id, model.captured_at == last)
            ).scalar_one()
        rows.append(CaptureRow(table=name, captured_at=last, rows=count))

    # A fixture has no `league_id`; its only route to a lega is through
    # `league_competition`. Resolving that in the query rather than in Python is safe here
    # — nothing is being deleted, which is the case where `LeagueRepository.purge` has to
    # resolve the ids first or the subquery sees its own deletes.
    comp_ids = select(LeagueCompetition.competition_id).where(
        LeagueCompetition.league_id == league_id
    )
    count, updated = session.execute(
        select(func.count(), func.max(LeagueFixture.updated_at)).where(
            LeagueFixture.competition_id.in_(comp_ids)
        )
    ).one()
    rows.append(CaptureRow(table="league_fixture", captured_at=updated, rows=count))
    return rows


__all__ = [
    "SNAPSHOT_TABLES",
    "CaptureRow",
    "all_league_ids",
    "capture_inventory",
    "latest_capture",
    "latest_rosters",
    "latest_settings",
]
