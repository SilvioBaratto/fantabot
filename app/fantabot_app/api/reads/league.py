"""Latest-capture reads for the lega snapshot tables."""

from __future__ import annotations

from fantabot.adapters.persistence.models.league import LeagueSnapshot, LeagueTeamSnapshot
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def all_league_ids(session: Session) -> list[int]:
    """Every league_id that has at least one snapshot, ascending."""
    stmt = select(LeagueSnapshot.league_id).distinct().order_by(LeagueSnapshot.league_id)
    return list(session.execute(stmt).scalars().all())


def latest_settings(session: Session, league_id: int) -> LeagueSnapshot | None:
    """The most recent LeagueSnapshot for one lega, or None."""
    stmt = (
        select(LeagueSnapshot)
        .where(LeagueSnapshot.league_id == league_id)
        .order_by(LeagueSnapshot.captured_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalars().first()


def latest_rosters(session: Session, league_id: int) -> list[LeagueTeamSnapshot]:
    """Every team's snapshot at the lega's most recent capture, ordered by team_id."""
    last = session.execute(
        select(func.max(LeagueTeamSnapshot.captured_at)).where(
            LeagueTeamSnapshot.league_id == league_id
        )
    ).scalar()
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
